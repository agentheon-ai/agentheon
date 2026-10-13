"""Learned knowledge: short notes the platform keeps between questions.

Safety rules, all enforced here:
- PII is redacted before anything is stored.
- Every learning expires. "data" learnings expire with the refresh cycle of the tool they came
  from; "behavioral" learnings (how people ask, what they prefer) live longer.
- Private runs never read or write knowledge (handled by the engine).
"""

from __future__ import annotations

import re

from sqlalchemy import and_, delete, insert, or_, select
from sqlalchemy.engine import Engine

from .db import learnings, now

DAY = 86400

_PII = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[card]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[id-number]"),
    (re.compile(r"(?:\+\d{1,3}[ .-]?)?\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"), "[phone]"),
]
_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "with", "that", "this", "what", "how", "are", "was", "you", "from", "about"}


def redact(text: str) -> str:
    for pattern, replacement in _PII:
        text = pattern.sub(replacement, text)
    return text


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


class Knowledge:
    def __init__(self, engine: Engine, behavioral_ttl_days: int = 90, web_ttl_days: int = 30):
        self.engine = engine
        self.behavioral_ttl_days = behavioral_ttl_days
        self.web_ttl_days = web_ttl_days

    def ttl_days(self, kind: str, tool_refresh_days: list[int | None]) -> int:
        if kind == "behavioral":
            return self.behavioral_ttl_days
        known = [d for d in tool_refresh_days if d]
        return min(known) if known else self.web_ttl_days

    def remember(self, text: str, kind: str, *, skill: str | None, user_id: str | None, ttl_days: int) -> None:
        text = redact(text.strip())[:500]
        if not text or kind not in {"behavioral", "data"}:
            return
        created = now()
        with self.engine.begin() as conn:
            conn.execute(
                insert(learnings).values(
                    text=text,
                    kind=kind,
                    skill=skill,
                    user_id=user_id,
                    created_at=created,
                    expires_at=created + ttl_days * DAY,
                )
            )

    def recall(self, question: str, user_id: str, limit: int = 5) -> list[str]:
        """Return the most relevant unexpired learnings: shared ones plus this user's own."""
        query = (
            select(learnings.c.text)
            .where(
                and_(
                    learnings.c.expires_at > now(),
                    or_(learnings.c.user_id.is_(None), learnings.c.user_id == user_id),
                )
            )
            .order_by(learnings.c.created_at.desc())
            .limit(500)
        )
        with self.engine.connect() as conn:
            rows = [r.text for r in conn.execute(query)]
        wanted = _words(question)
        scored = [(len(wanted & _words(text)), text) for text in rows]
        return [text for score, text in sorted(scored, key=lambda x: -x[0]) if score > 0][:limit]

    def list(self, user_id: str, limit: int = 50) -> list[dict]:
        """Unexpired learnings this user's questions can draw on, newest first."""
        query = (
            select(learnings.c.id, learnings.c.text, learnings.c.kind, learnings.c.skill,
                   learnings.c.user_id, learnings.c.created_at, learnings.c.expires_at)
            .where(
                and_(
                    learnings.c.expires_at > now(),
                    or_(learnings.c.user_id.is_(None), learnings.c.user_id == user_id),
                )
            )
            .order_by(learnings.c.created_at.desc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [
            {"id": r["id"], "text": r["text"], "kind": r["kind"], "skill": r["skill"],
             "shared": r["user_id"] is None, "created_at": r["created_at"], "expires_at": r["expires_at"]}
            for r in rows
        ]

    def purge_expired(self) -> int:
        with self.engine.begin() as conn:
            return conn.execute(delete(learnings).where(learnings.c.expires_at <= now())).rowcount
