"""The harness. One question goes: guard -> plan -> run skills -> gates -> synthesize -> learn -> audit.

Every consumer (web, REST, MCP, watches) calls Engine.run() and gets the same result shape:

    {"run_id": ..., "status": "succeeded" | "needs_user_input" | "failed",
     "answer": ..., "question": ..., "error": ...,
     "provenance": {"skills": [...], "tools": [...], "sources": [...], "memory_used": n},
     "gates": {...}, "trace": [...]}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import insert, update
from sqlalchemy.engine import Engine as DBEngine
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .db import now, runs
from .governance import Policy, ground_sources
from .knowledge import Knowledge
from .llm import LLM, LLMOutputError, complete_json, parse_json
from .skills import Skill, SkillRegistry
from .tools import ToolError, ToolRegistry

log = logging.getLogger("agentheon")

Emit = Callable[[str, dict], Awaitable[None]]
MAX_SKILLS_PER_QUESTION = 3
_TAG = re.compile(r"(?<![\w@])@([a-z][a-z0-9_]{1,63})\b")
_RETRY_TAIL = re.compile(r"\s*LiteLLM Retried:.*$", re.DOTALL)  # litellm appends its own retry count
# Provider errors are stored in the runs table and returned to whoever asked, so scrub anything
# key-shaped before it gets there. Most providers mask the key themselves; this does not rely on it.
_SECRET = re.compile(r"(?i)\b(?:sk|pk|rk|api)[-_][A-Za-z0-9_\-]{8,}|\bBearer\s+[A-Za-z0-9._\-]{8,}")


async def _no_emit(event: str, data: dict) -> None:
    return None


@dataclass
class SkillOutcome:
    skill: str
    status: str  # succeeded | needs_user_input | failed
    answer: str = ""
    question: str = ""
    error: str = ""
    sources: list[str] = field(default_factory=list)
    observed: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    dropped_sources: list[str] = field(default_factory=list)
    regenerated: bool = False


def retry_feedback(violations: list[str], cited: list[str], observed: list[str]) -> str:
    """Tell a rejected skill what was wrong *and* how to fix it.

    A bare list of violations is not enough for smaller models. Told only to "cite at least one
    source returned by a tool", they rewrite the prose and leave the `sources` array empty again,
    burning the single regeneration and failing the run. The engine already knows every source the
    tools returned, so naming the field and listing those sources turns a guess into a copy.
    """
    parts = ["Your answer was rejected by the compliance gate:"]
    parts += [f"- {v}" for v in violations]
    if any("source" in v.lower() for v in violations):
        if observed:
            parts += [
                "",
                f'The "sources" field of your reply was {json.dumps(cited)}. These are the only '
                "sources you may cite, copied exactly from your own tool results:",
            ]
            parts += [f"- {s}" for s in dict.fromkeys(observed)]
            parts.append('Put the ones you relied on in the "sources" array itself — a URL written '
                         "only in the answer text does not count.")
        else:
            parts += ["", "No tool has returned a source yet. Call a tool that returns one before "
                          "answering."]
    parts.append("Fix every point and answer again. You may call tools first.")
    return "\n".join(parts)


def describe_failure(exc: Exception) -> str:
    """Explain a crashed run in terms of what actually went wrong.

    Model-provider errors are the common case, and the provider's own wording is what makes them
    actionable. A billing problem, an unknown model name and a revoked key are three different
    fixes, so reporting all of them as "check LLM_MODEL and its API key" sends people to look at
    the wrong thing. Anything that is not a provider error stays vague on purpose: it is a bug in
    us, the traceback is in the log, and the person asking the question cannot act on it.
    """
    name = type(exc).__name__
    if "litellm" not in type(exc).__module__:
        return f"{name} during the run. Details are in the server log."
    detail = _RETRY_TAIL.sub("", str(exc)).strip()
    try:  # providers nest the useful sentence inside a JSON error envelope
        error = parse_json(detail).get("error")
    except LLMOutputError:
        error = None
    if isinstance(error, dict) and error.get("message"):
        detail = str(error["message"])
    if not detail:
        return f"{name} from the model provider. Details are in the server log."
    return f"{name} from the model provider: {_SECRET.sub('[redacted]', detail)[:400]}"


def result(status: str, **fields) -> dict:
    base = {
        "status": status,
        "answer": "",
        "question": "",
        "error": "",
        "provenance": {"skills": [], "tools": [], "sources": [], "memory_used": 0},
        "gates": {},
        "trace": [],
    }
    base.update(fields)
    return base


class Engine:
    def __init__(
        self,
        *,
        settings: Settings,
        llm: LLM,
        skills: SkillRegistry,
        tools: ToolRegistry,
        policy: Policy,
        knowledge: Knowledge,
        db: DBEngine,
    ):
        self.settings = settings
        self.llm = llm
        self.skills = skills
        self.tools = tools
        self.policy = policy
        self.knowledge = knowledge
        self.db = db

    # ---------------------------------------------------------------- public entry point

    async def run(
        self,
        question: str,
        *,
        user_id: str = "anonymous",
        skills: list[str] | None = None,
        private: bool = False,
        emit: Emit | None = None,
        run_id: str | None = None,
    ) -> dict:
        emit = emit or _no_emit
        run_id = run_id or str(uuid.uuid4())
        self.record_start(run_id, user_id, question)
        try:
            outcome = await asyncio.wait_for(
                self._run(question, user_id=user_id, requested=skills or [], private=private, emit=emit),
                timeout=self.settings.run_timeout_seconds,
            )
        except TimeoutError:
            outcome = result("failed", error=f"Timed out after {self.settings.run_timeout_seconds}s")
        except Exception as exc:
            log.exception("Run %s crashed", run_id)
            outcome = result("failed", error=describe_failure(exc))
        outcome["run_id"] = run_id
        self._record_finish(run_id, outcome)
        await emit("result", outcome)
        return outcome

    # ---------------------------------------------------------------- the pipeline

    async def _run(self, question: str, *, user_id: str, requested: list[str], private: bool, emit: Emit) -> dict:
        question = question.strip()
        if not question:
            return result("failed", error="Question is empty.")
        if len(question) > self.settings.max_question_chars:
            return result("failed", error=f"Question is longer than {self.settings.max_question_chars} characters.")

        # 1. Plan: explicit skills (API param or @tags) bypass the planner.
        requested = list(dict.fromkeys(requested + _TAG.findall(question)))
        if requested:
            unknown = [s for s in requested if not self.skills.get(s)]
            if unknown:
                return result("failed", error=f"Unknown skill(s): {', '.join(unknown)}")
            selected, inputs = requested[:MAX_SKILLS_PER_QUESTION], {}
        else:
            plan = await self._plan(question)
            if plan["status"] != "ok":
                return plan
            selected, inputs = plan["skills"], plan["inputs"]
        await emit("plan", {"skills": selected, "inputs": inputs})

        # 2. Recall what was learned before (never for private runs).
        memory = [] if private else self.knowledge.recall(question, user_id)

        # 3. Run the chosen skills in parallel; each has its own loop and its own gate.
        outcomes: list[SkillOutcome] = await asyncio.gather(
            *[self._run_skill(self.skills.get(name), question, inputs, memory, emit) for name in selected]
        )
        trace = [{"skill": o.skill, "status": o.status, "tools": o.tools_used} for o in outcomes]
        for o in outcomes:
            if o.status == "needs_user_input":
                return result("needs_user_input", question=o.question, trace=trace)
        good = [o for o in outcomes if o.status == "succeeded"]
        gates = {
            o.skill: {
                "compliance": "pass" if o.status == "succeeded" else "fail",
                "violations": o.violations,
                "regenerated": o.regenerated,
                "dropped_sources": o.dropped_sources,
            }
            for o in outcomes
        }
        if not good:
            errors = "; ".join(f"{o.skill}: {o.error}" for o in outcomes)
            return result("failed", error=errors, gates=gates, trace=trace,
                          provenance=self._provenance(outcomes, [], len(memory)))

        # 4. Synthesize (only needed when more than one skill answered), then gate the final answer.
        if len(good) == 1:
            answer, sources = good[0].answer, good[0].sources
        else:
            answer, sources, final_violations = await self._synthesize(question, good)
            gates["final"] = {"compliance": "fail" if final_violations else "pass", "violations": final_violations}
            if final_violations:
                return result("failed", error="Final answer failed governance: " + "; ".join(final_violations),
                              gates=gates, trace=trace, provenance=self._provenance(outcomes, [], len(memory)))

        partial = [o.skill for o in outcomes if o.status != "succeeded"]
        if partial:
            answer += f"\n\n_Note: these skills could not contribute: {', '.join(partial)}._"

        # 5. Learn (best effort, never blocks the answer).
        if not private and self.settings.learning_enabled:
            await self._learn(question, answer, good, user_id)

        return result("succeeded", answer=answer, gates=gates, trace=trace,
                      provenance=self._provenance(outcomes, sources, len(memory)))

    # ---------------------------------------------------------------- planner

    async def _plan(self, question: str) -> dict:
        cards = [s.card() for s in self.skills.all()]
        if not cards:
            return result("failed", error="No skills are installed.")
        system = (
            "You route questions for an assistant. Pick the skills needed to answer.\n"
            f"Skills:\n{json.dumps(cards, indent=1)}\n\n"
            "Reply with ONE JSON object:\n"
            '{"status": "ok", "skills": ["<name>"], "inputs": {"<required_input>": "<value>"}}\n'
            '{"status": "out_of_scope", "reason": "<why no skill fits>"}\n'
            '{"status": "needs_user_input", "question": "<what to ask the user>"}\n'
            f"Choose the fewest skills that fully answer (max {MAX_SKILLS_PER_QUESTION}). Fill each chosen "
            "skill's required_inputs from the question, in the user's own words: the skill works out "
            "identifiers, URLs and other specifics itself. Ask only when the question does not mention "
            "an input at all. Never invent values."
        )
        try:
            plan = await complete_json(self.llm, [{"role": "system", "content": system},
                                                   {"role": "user", "content": question}])
        except LLMOutputError as exc:
            return result("failed", error=f"Planner returned invalid output: {exc}")

        status = plan.get("status")
        if status == "out_of_scope":
            return result("failed", error="Out of scope: " + str(plan.get("reason", "no skill fits this question")))
        if status == "needs_user_input":
            return result("needs_user_input", question=str(plan.get("question", "Can you add more detail?")))

        names = [n for n in plan.get("skills") or [] if self.skills.get(str(n))][:MAX_SKILLS_PER_QUESTION]
        if not names:
            return result("failed", error="Out of scope: no installed skill fits this question.")
        inputs = plan.get("inputs") if isinstance(plan.get("inputs"), dict) else {}
        missing = [i for n in names for i in self.skills.get(n).required_inputs if not str(inputs.get(i, "")).strip()]
        if missing:
            return result("needs_user_input", question=f"Please provide: {', '.join(dict.fromkeys(missing))}.")
        return {"status": "ok", "skills": names, "inputs": inputs}

    # ---------------------------------------------------------------- one skill's loop

    def _skill_prompt(self, skill: Skill, memory: list[str]) -> str:
        parts = [f"You are the skill '{skill.name}': {skill.description}", "", "Instructions:", skill.instructions]
        if block := self.policy.prompt_block():
            parts += ["", block]
        if memory:
            parts += ["", "Notes learned earlier (may be outdated; verify anything important):"]
            parts += [f"- {m}" for m in memory]
        parts += [
            "",
            f"Tools you can call:\n{json.dumps(self.tools.specs(skill.tools), indent=1) if skill.tools else 'none'}",
            "",
            "Reply with exactly ONE JSON object per turn:",
            '{"action": "tool", "tool": "<name>", "args": {...}}',
            '{"action": "answer", "answer": "<markdown>", "sources": ["<url or id copied from tool results>"]}',
            '{"action": "ask", "question": "<the one thing you need from the user>"}',
            "Only cite sources that appeared in tool results. Use tools for facts; never invent data.",
        ]
        return "\n".join(parts)

    async def _run_skill(
        self, skill: Skill, question: str, inputs: dict, memory: list[str], emit: Emit
    ) -> SkillOutcome:
        out = SkillOutcome(skill=skill.name, status="failed")
        user_msg = question if not inputs else f"{question}\n\nInputs: {json.dumps(inputs)}"
        messages = [{"role": "system", "content": self._skill_prompt(skill, memory)},
                    {"role": "user", "content": user_msg}]
        max_steps = skill.max_steps or self.settings.max_steps
        await emit("skill_started", {"skill": skill.name})

        for _ in range(max_steps):
            try:
                decision = await complete_json(self.llm, messages)
            except LLMOutputError as exc:
                out.error = f"Model returned invalid output: {exc}"
                return out
            messages.append({"role": "assistant", "content": json.dumps(decision)})
            action = decision.get("action")

            if action == "tool":
                feedback = await self._call_tool(skill, decision, out, emit)
                messages.append({"role": "user", "content": feedback})

            elif action == "ask":
                out.status, out.question = "needs_user_input", str(decision.get("question", "")).strip()
                return out

            elif action == "answer":
                answer = str(decision.get("answer", "")).strip()
                cited = [str(s) for s in decision.get("sources") or []]
                kept, dropped = ground_sources(cited, out.observed)
                violations = self.policy.check(answer, kept, skill.name)
                if not answer:
                    violations.append("[empty] The answer is empty.")
                if skill.require_sources and not kept:
                    # worded so it reads differently from a governance require_sources rule, which
                    # fires on the same fact and otherwise prints an identical sentence
                    violations.append(f"[grounding] Skill {skill.name!r} is set to require_sources "
                                      "and this answer cites none.")
                out.dropped_sources = dropped
                if not violations:
                    out.status, out.answer, out.sources = "succeeded", answer, kept
                    await emit("skill_done", {"skill": skill.name})
                    return out
                out.violations = violations
                if out.regenerated:  # already had our one retry: fail closed
                    out.error = "Answer failed governance after one regeneration: " + "; ".join(violations)
                    await emit("gate_failed", {"skill": skill.name, "violations": violations})
                    return out
                out.regenerated = True
                await emit("gate_retry", {"skill": skill.name, "violations": violations})
                messages.append({"role": "user",
                                 "content": retry_feedback(violations, cited, out.observed)})
            else:
                messages.append({"role": "user", "content": "Unknown action. Use tool, answer or ask."})

        out.error = f"Step budget of {max_steps} used up before an answer."
        return out

    async def _call_tool(self, skill: Skill, decision: dict, out: SkillOutcome, emit: Emit) -> str:
        name = str(decision.get("tool", ""))
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        tool = self.tools.get(name)
        if name not in skill.tools or tool is None:
            return f"Tool '{name}' is not available. Available: {', '.join(skill.tools) or 'none'}."
        await emit("tool_call", {"skill": skill.name, "tool": name, "args": args})
        sources: list[str] = []
        try:
            res = await tool.call(args)
            content, sources = res.content, res.sources
        except ToolError as exc:
            content = f"Error: {exc}"
        except TypeError as exc:
            content = f"Error: bad arguments for {name}: {exc}"
        except Exception:
            log.exception("Tool %s failed", name)
            content = f"Error: {name} failed unexpectedly."
        out.tools_used.append(name)
        out.observed.extend(sources)
        await emit("tool_result", {"skill": skill.name, "tool": name, "preview": content[:300], "sources": sources})
        return f"Result of {name}:\n{content}\nSources: {json.dumps(sources)}"

    # ---------------------------------------------------------------- synthesizer

    async def _synthesize(self, question: str, outcomes: list[SkillOutcome]) -> tuple[str, list[str], list[str]]:
        parts = [f"## From {o.skill}\n{o.answer}\nSources: {json.dumps(o.sources)}" for o in outcomes]
        system = (
            "Merge the skill answers below into one clear answer to the user's question. Keep every fact "
            "attributable; do not add facts. Reply with JSON: "
            '{"answer": "<markdown>", "sources": ["<sources you relied on, copied exactly>"]}'
        )
        allowed = [s for o in outcomes for s in o.sources]
        try:
            merged = await complete_json(self.llm, [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question: {question}\n\n" + "\n\n".join(parts)},
            ])
        except LLMOutputError:
            # fall back to stitching the answers together rather than failing the run
            answer = "\n\n".join(f"**{o.skill}**\n\n{o.answer}" for o in outcomes)
            return answer, list(dict.fromkeys(allowed)), []
        answer = str(merged.get("answer", "")).strip()
        sources, _ = ground_sources([str(s) for s in merged.get("sources") or []], allowed)
        violations = self.policy.check(answer, sources, "*") if answer else ["[empty] The merged answer is empty."]
        return answer, sources, violations

    # ---------------------------------------------------------------- learning

    async def _learn(self, question: str, answer: str, outcomes: list[SkillOutcome], user_id: str) -> None:
        system = (
            "Extract at most 3 short, reusable notes from this exchange that would help answer future "
            "questions. kind='data' for facts that came from tool results; kind='behavioral' for how this "
            "user asks or what they prefer. Never include personal data, names, raw tables or one-off "
            'details. Reply with JSON: {"learnings": [{"text": "...", "kind": "data|behavioral"}]} '
            "(empty list if nothing is reusable)."
        )
        try:
            data = await complete_json(self.llm, [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question: {question}\n\nAnswer: {answer[:4000]}"},
            ])
        except Exception:
            log.warning("Learning extraction failed; skipping", exc_info=True)
            return
        tools_used = {t for o in outcomes for t in o.tools_used}
        refresh = [self.tools.get(t).refresh_days for t in tools_used if self.tools.get(t)]
        skill = outcomes[0].skill if len(outcomes) == 1 else None
        for item in (data.get("learnings") or [])[:3]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind not in {"data", "behavioral"}:
                continue
            self.knowledge.remember(
                str(item.get("text", "")),
                kind,
                skill=skill,
                user_id=user_id if kind == "behavioral" else None,  # preferences stay personal
                ttl_days=self.knowledge.ttl_days(kind, refresh),
            )

    # ---------------------------------------------------------------- audit

    @staticmethod
    def _provenance(outcomes: list[SkillOutcome], sources: list[str], memory_used: int) -> dict:
        return {
            "skills": [o.skill for o in outcomes],
            "tools": sorted({t for o in outcomes for t in o.tools_used}),
            "sources": sources,
            "memory_used": memory_used,
        }

    def record_start(self, run_id: str, user_id: str, question: str) -> None:
        """Record a run as 'running'. Safe to call twice (the API records before spawning a background run)."""
        try:
            with self.db.begin() as conn:
                conn.execute(insert(runs).values(id=run_id, user_id=user_id, question=question[:20000],
                                                 status="running", created_at=now()))
        except IntegrityError:
            pass

    def _record_finish(self, run_id: str, outcome: dict) -> None:
        with self.db.begin() as conn:
            conn.execute(update(runs).where(runs.c.id == run_id).values(
                status=outcome["status"], result_json=json.dumps(outcome, default=str), finished_at=now()))
