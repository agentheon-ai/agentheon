"""Watches: re-ask a question on a schedule, keep every run, and notify when the answer meaningfully changes.

The scheduler is a simple loop in the API process (RUN_SCHEDULER=true). In a multi-replica deploy,
enable it on exactly one replica. Each watch is claimed by moving next_run_at forward *before*
running it, so a crash never causes a tight retry loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid

import httpx
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine as DBEngine

from .db import now, watch_runs, watches
from .engine import Engine
from .llm import LLMOutputError, complete_json

log = logging.getLogger("agentheon.watches")

SEVERITY = {"none": 0, "minor": 1, "notable": 2, "major": 3}
THRESHOLDS = {"minor", "notable", "major"}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def answer_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()


class WatchError(ValueError):
    pass


class WatchService:
    def __init__(self, db: DBEngine, engine: Engine, webhook_url: str | None = None):
        self.db = db
        self.engine = engine
        self.webhook_url = webhook_url

    # ---------------------------------------------------------------- CRUD

    def create(self, *, owner: str, name: str, question: str, skills: list[str] | None = None,
               interval_minutes: int = 1440, threshold: str = "notable", history_window: int = 5,
               max_runs: int | None = None) -> dict:
        if interval_minutes < 5:
            raise WatchError("interval_minutes must be at least 5")
        if threshold not in THRESHOLDS:
            raise WatchError(f"threshold must be one of {sorted(THRESHOLDS)}")
        if not 1 <= history_window <= 50:
            raise WatchError("history_window must be between 1 and 50")
        unknown = [s for s in skills or [] if not self.engine.skills.get(s)]
        if unknown:
            raise WatchError(f"Unknown skill(s): {', '.join(unknown)}")
        row = {
            "id": str(uuid.uuid4()), "owner": owner, "name": name.strip()[:200] or "Untitled watch",
            "question": question.strip(), "skills": ",".join(skills or []),
            "interval_minutes": interval_minutes, "threshold": threshold, "history_window": history_window,
            "max_runs": max_runs, "runs_done": 0, "active": True,
            "next_run_at": now(),  # first run happens on the next scheduler tick
            "created_at": now(),
        }
        with self.db.begin() as conn:
            conn.execute(insert(watches).values(**row))
        return row

    def get(self, watch_id: str, owner: str | None = None) -> dict | None:
        query = select(watches).where(watches.c.id == watch_id)
        if owner is not None:
            query = query.where(watches.c.owner == owner)
        with self.db.connect() as conn:
            row = conn.execute(query).mappings().first()
        return dict(row) if row else None

    def list(self, owner: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(select(watches).where(watches.c.owner == owner)
                                .order_by(watches.c.created_at.desc())).mappings().all()
        return [dict(r) for r in rows]

    def runs(self, watch_id: str, limit: int = 50) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(select(watch_runs).where(watch_runs.c.watch_id == watch_id)
                                .order_by(watch_runs.c.id.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def set_active(self, watch_id: str, active: bool) -> None:
        values = {"active": active}
        if active:
            values["next_run_at"] = now()
        with self.db.begin() as conn:
            conn.execute(update(watches).where(watches.c.id == watch_id).values(**values))

    def delete(self, watch_id: str) -> None:
        with self.db.begin() as conn:
            conn.execute(delete(watch_runs).where(watch_runs.c.watch_id == watch_id))
            conn.execute(delete(watches).where(watches.c.id == watch_id))

    # ---------------------------------------------------------------- running

    async def run_once(self, watch_id: str) -> dict:
        watch = self.get(watch_id)
        if not watch:
            raise WatchError("Watch not found")
        skills = [s for s in (watch["skills"] or "").split(",") if s]
        outcome = await self.engine.run(watch["question"], user_id=watch["owner"], skills=skills or None)

        previous = [r for r in self.runs(watch_id, limit=watch["history_window"]) if r["status"] == "succeeded"]
        severity, summary = "none", ""
        answer = outcome.get("answer", "")
        digest = answer_hash(answer) if answer else None
        if outcome["status"] != "succeeded":
            summary = outcome.get("error") or outcome.get("question") or "Run did not succeed."
        elif not previous:
            summary = "First run; baseline recorded."
        elif digest != previous[0]["answer_hash"]:
            severity, summary = await self._judge(watch["question"], previous, answer)

        notify = SEVERITY[severity] >= SEVERITY[watch["threshold"]]
        row = {
            "watch_id": watch_id, "run_id": outcome["run_id"], "status": outcome["status"], "answer": answer,
            "answer_hash": digest, "severity": severity, "summary": summary, "notified": notify,
            "created_at": now(),
        }
        with self.db.begin() as conn:
            conn.execute(insert(watch_runs).values(**row))
            runs_done = watch["runs_done"] + 1
            done = bool(watch["max_runs"]) and runs_done >= watch["max_runs"]
            conn.execute(update(watches).where(watches.c.id == watch_id)
                         .values(runs_done=runs_done, active=False if done else watch["active"]))
        if notify:
            await self._notify(watch, row)
        return row

    async def _judge(self, question: str, previous: list[dict], new_answer: str) -> tuple[str, str]:
        history = "\n\n".join(f"Earlier answer {i + 1} (newest first):\n{r['answer'][:3000]}"
                              for i, r in enumerate(previous))
        system = (
            "You compare a new answer with earlier answers to the same recurring question and judge "
            "how much really changed. Ignore wording, ordering and formatting differences. "
            "Severity: none (same facts), minor (small detail), notable (a fact or number a reader should "
            "know about changed), major (the conclusion changed). Reply with JSON: "
            '{"severity": "none|minor|notable|major", "summary": "<what changed, one or two sentences>"}'
        )
        try:
            verdict = await complete_json(self.engine.llm, [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question: {question}\n\n{history}\n\nNew answer:\n{new_answer[:3000]}"},
            ])
        except LLMOutputError:
            return "minor", "Answer text changed; the change could not be classified."
        severity = str(verdict.get("severity", "minor")).lower()
        return (severity if severity in SEVERITY else "minor"), str(verdict.get("summary", ""))[:1000]

    async def _notify(self, watch: dict, run: dict) -> None:
        if not self.webhook_url:
            return  # the change is still recorded and visible in the UI and API
        payload = {"watch_id": watch["id"], "name": watch["name"], "owner": watch["owner"],
                   "severity": run["severity"], "summary": run["summary"], "run_id": run["run_id"]}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self.webhook_url, content=json.dumps(payload),
                                  headers={"Content-Type": "application/json"})
        except httpx.HTTPError:
            log.warning("Webhook notification failed for watch %s", watch["id"], exc_info=True)

    # ---------------------------------------------------------------- scheduler

    def _claim_due(self) -> list[str]:
        """Return due watch ids and push each one's next_run_at forward in the same transaction."""
        claimed = []
        with self.db.begin() as conn:
            due = conn.execute(select(watches.c.id, watches.c.interval_minutes)
                               .where(watches.c.active.is_(True), watches.c.next_run_at <= now())
                               .limit(20)).all()
            for watch_id, interval in due:
                result = conn.execute(update(watches)
                                      .where(watches.c.id == watch_id, watches.c.next_run_at <= now())
                                      .values(next_run_at=now() + interval * 60))
                if result.rowcount:
                    claimed.append(watch_id)
        return claimed

    async def tick(self) -> int:
        ids = self._claim_due()
        for watch_id in ids:
            try:
                await self.run_once(watch_id)
            except Exception:
                log.exception("Watch %s failed", watch_id)
        return len(ids)

    async def loop(self, poll_seconds: int, purge: callable | None = None) -> None:
        log.info("Watch scheduler started (every %ss)", poll_seconds)
        while True:
            try:
                await self.tick()
                if purge:
                    purge()
            except Exception:
                log.exception("Scheduler tick failed")
            await asyncio.sleep(poll_seconds)
