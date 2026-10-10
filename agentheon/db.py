"""Storage: one SQLAlchemy engine, four tables. SQLite locally, Postgres in production (same code)."""

from __future__ import annotations

import time

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

# Every question asked is a run. Together they are the audit trail.
runs = Table(
    "runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(200), nullable=False, index=True),
    Column("question", Text, nullable=False),
    Column("status", String(32), nullable=False),  # running | succeeded | needs_user_input | failed
    Column("result_json", Text),  # full status contract, including provenance and gate results
    Column("created_at", Float, nullable=False),
    Column("finished_at", Float),
)

# What the platform learned. Every row expires.
learnings = Table(
    "learnings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("text", Text, nullable=False),
    Column("kind", String(20), nullable=False),  # behavioral | data
    Column("skill", String(100)),
    Column("user_id", String(200)),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False, index=True),
)

watches = Table(
    "watches",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("owner", String(200), nullable=False, index=True),
    Column("name", String(200), nullable=False),
    Column("question", Text, nullable=False),
    Column("skills", Text),  # comma-separated, empty = let the planner choose
    Column("interval_minutes", Integer, nullable=False),
    Column("threshold", String(20), nullable=False),  # minor | notable | major
    Column("history_window", Integer, nullable=False),
    Column("max_runs", Integer),
    Column("runs_done", Integer, nullable=False, default=0),
    Column("active", Boolean, nullable=False, default=True),
    Column("next_run_at", Float, nullable=False, index=True),
    Column("created_at", Float, nullable=False),
)

watch_runs = Table(
    "watch_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("watch_id", String(36), nullable=False, index=True),
    Column("run_id", String(36)),
    Column("status", String(32), nullable=False),
    Column("answer", Text),
    Column("answer_hash", String(64)),
    Column("severity", String(20), nullable=False),  # none | minor | notable | major
    Column("summary", Text),
    Column("notified", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
)


def make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    engine = create_engine(url, **kwargs)
    metadata.create_all(engine)
    return engine


def now() -> float:
    return time.time()
