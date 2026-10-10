"""End-to-end behaviour of the harness, with a scripted model."""

import json

from sqlalchemy import select

from agentheon.db import learnings, runs

PLAN = {"status": "ok", "skills": ["docs"], "inputs": {"topic": "pricing"}}
LOOKUP = {"action": "tool", "tool": "lookup", "args": {"topic": "pricing"}}
ANSWER = {"action": "answer", "answer": "The Pro plan costs 20 per month.", "sources": ["doc://pricing"]}


async def test_happy_path_with_provenance_and_audit(make_platform):
    platform, llm = make_platform(PLAN, LOOKUP, ANSWER)
    events = []

    async def emit(event, data):
        events.append(event)

    result = await platform.engine.run("How much is Pro?", user_id="alice", emit=emit)

    assert result["status"] == "succeeded"
    assert result["answer"] == "The Pro plan costs 20 per month."
    assert result["provenance"]["skills"] == ["docs"]
    assert result["provenance"]["tools"] == ["lookup"]
    assert result["provenance"]["sources"] == ["doc://pricing"]
    assert events[0] == "plan" and "tool_call" in events and events[-1] == "result"
    # the tool result was fed back to the model
    assert "The Pro plan costs 20 per month." in llm.calls[2][-1]["content"]
    with platform.db.connect() as conn:
        row = conn.execute(select(runs).where(runs.c.id == result["run_id"])).mappings().one()
    assert row["status"] == "succeeded" and row["user_id"] == "alice"
    assert json.loads(row["result_json"])["answer"] == result["answer"]


async def test_tagged_skill_skips_planner(make_platform):
    platform, llm = make_platform(LOOKUP, ANSWER)
    result = await platform.engine.run("@docs pricing please")
    assert result["status"] == "succeeded"
    assert len(llm.calls) == 2  # no planner call


async def test_unknown_tagged_skill_fails(make_platform):
    platform, _ = make_platform()
    result = await platform.engine.run("hi", skills=["nope"])
    assert result["status"] == "failed" and "nope" in result["error"]


async def test_out_of_scope(make_platform):
    platform, _ = make_platform({"status": "out_of_scope", "reason": "Not about our product."})
    result = await platform.engine.run("Who won the match?")
    assert result["status"] == "failed" and "Out of scope" in result["error"]


async def test_missing_required_input_asks_instead_of_guessing(make_platform):
    platform, llm = make_platform({"status": "ok", "skills": ["docs"], "inputs": {}})
    result = await platform.engine.run("Tell me about the plan")
    assert result["status"] == "needs_user_input"
    assert "topic" in result["question"]
    assert len(llm.calls) == 1  # never ran the skill


async def test_skill_can_ask_the_user(make_platform):
    platform, _ = make_platform(PLAN, {"action": "ask", "question": "Which plan do you mean?"})
    result = await platform.engine.run("price?")
    assert result["status"] == "needs_user_input" and result["question"] == "Which plan do you mean?"


async def test_grounding_rejects_invented_sources_then_fails_closed(make_platform):
    invented = {"action": "answer", "answer": "It costs 5.", "sources": ["https://made-up.example"]}
    platform, _ = make_platform(PLAN, invented, invented)
    result = await platform.engine.run("How much is Pro?")
    assert result["status"] == "failed"
    gate = result["gates"]["docs"]
    assert gate["regenerated"] is True
    assert gate["dropped_sources"] == ["https://made-up.example"]
    assert any("grounding" in v for v in gate["violations"])


GOV = """
rules:
  - {id: no_advice, type: forbid, pattern: "(?i)you should buy", message: Do not give buying advice.}
"""


async def test_compliance_gate_regenerates_once_and_passes(make_platform):
    bad = {"action": "answer", "answer": "You should buy Pro.", "sources": ["doc://pricing"]}
    platform, llm = make_platform(PLAN, LOOKUP, bad, ANSWER, governance=GOV)
    result = await platform.engine.run("Should I get Pro?")
    assert result["status"] == "succeeded"
    assert result["gates"]["docs"]["regenerated"] is True
    assert "rejected by the compliance gate" in llm.calls[3][-1]["content"]
    assert "Do not give buying advice" in llm.calls[1][0]["content"]  # rules are in the prompt too


async def test_compliance_gate_fails_closed(make_platform):
    bad = {"action": "answer", "answer": "You should buy Pro.", "sources": ["doc://pricing"]}
    platform, _ = make_platform(PLAN, LOOKUP, bad, bad, governance=GOV)
    result = await platform.engine.run("Should I get Pro?")
    assert result["status"] == "failed"
    assert result["answer"] == ""
    assert "no_advice" in result["error"]


async def test_tool_errors_are_fed_back_not_fatal(make_platform):
    wrong_tool = {"action": "tool", "tool": "calculator", "args": {"expression": "1+1"}}
    platform, llm = make_platform(PLAN, wrong_tool, LOOKUP, ANSWER)
    result = await platform.engine.run("How much is Pro?")
    assert result["status"] == "succeeded"
    assert "not available" in llm.calls[2][-1]["content"]


async def test_step_budget(make_platform):
    platform, _ = make_platform(PLAN, *[LOOKUP] * 5)
    result = await platform.engine.run("How much is Pro?")
    assert result["status"] == "failed" and "Step budget" in result["error"]


async def test_invalid_model_output_retries_then_fails(make_platform):
    platform, _ = make_platform(PLAN, "not json", "still not json")
    result = await platform.engine.run("How much is Pro?")
    assert result["status"] == "failed" and "invalid output" in result["error"]


async def test_multi_skill_synthesis(make_platform):
    plan = {"status": "ok", "skills": ["docs", "calculator"], "inputs": {"topic": "pricing"}}
    calc = {"action": "answer", "answer": "Yearly: 240.", "sources": []}
    merged = {"answer": "Pro is 20 per month, 240 per year.", "sources": ["doc://pricing", "doc://fake"]}
    platform, llm = make_platform(plan, LOOKUP, ANSWER, calc, merged)

    # the two skills run concurrently; make the fake model answer by skill name
    order = {"docs": [LOOKUP, ANSWER], "calculator": [calc]}

    async def complete(messages, *, json_mode=False):
        llm.calls.append(list(messages))
        system = messages[0]["content"]
        for name, replies in order.items():
            if f"skill '{name}'" in system:
                return json.dumps(replies.pop(0))
        return json.dumps(plan if "route questions" in system else merged)

    llm.complete = complete
    result = await platform.engine.run("Pro price per month and per year?")
    assert result["status"] == "succeeded"
    assert result["answer"] == "Pro is 20 per month, 240 per year."
    assert result["provenance"]["sources"] == ["doc://pricing"]  # invented source dropped
    assert set(result["provenance"]["skills"]) == {"docs", "calculator"}


async def test_learning_is_redacted_scoped_and_skipped_when_private(make_platform):
    learned = {"learnings": [
        {"text": "Pro pricing is 20 per month (ask billing@example.com)", "kind": "data"},
        {"text": "User likes short answers", "kind": "behavioral"},
        {"text": "ignored", "kind": "secret"},
    ]}
    platform, llm = make_platform(PLAN, LOOKUP, ANSWER, learned, learning_enabled=True)
    await platform.engine.run("How much is Pro?", user_id="alice")
    with platform.db.connect() as conn:
        rows = {r.kind: r for r in conn.execute(select(learnings))}
    assert set(rows) == {"data", "behavioral"}
    assert "[email]" in rows["data"].text and rows["data"].user_id is None
    assert rows["behavioral"].user_id == "alice"
    assert round((rows["data"].expires_at - rows["data"].created_at) / 86400) == 14  # lookup refresh_days

    # private run: no learning call, no memory in the prompt
    llm.add(PLAN, LOOKUP, ANSWER)
    await platform.engine.run("How much is Pro pricing?", user_id="alice", private=True)
    assert llm.replies == []
    assert "Notes learned earlier" not in llm.calls[-2][0]["content"]

    # normal run: memory is recalled into the skill prompt
    llm.add(PLAN, LOOKUP, ANSWER, {"learnings": []})
    result = await platform.engine.run("Pro pricing?", user_id="alice")
    assert result["provenance"]["memory_used"] >= 1
    assert "Notes learned earlier" in llm.calls[-3][0]["content"]


async def test_question_guard(make_platform):
    platform, _ = make_platform(max_question_chars=10)
    assert (await platform.engine.run("x" * 11))["status"] == "failed"
    assert (await platform.engine.run("   "))["status"] == "failed"
