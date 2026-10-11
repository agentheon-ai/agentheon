"""Wires everything together. API, CLI, MCP server and tests all build the platform through build()."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.engine import Engine as DBEngine

from .builtin_tools import register_builtins
from .config import Settings
from .db import make_engine, now, runs
from .engine import Engine
from .governance import Policy, load_policy
from .knowledge import Knowledge
from .llm import LLM, LiteLLMClient
from .skills import SkillRegistry
from .tools import ToolRegistry
from .watches import WatchService

log = logging.getLogger("agentheon")


@dataclass
class Platform:
    settings: Settings
    db: DBEngine
    tools: ToolRegistry
    skills: SkillRegistry
    policy: Policy
    knowledge: Knowledge
    engine: Engine
    watches: WatchService
    problems: list[str]  # skill/tool load problems, shown on /ready


def build(settings: Settings | None = None, llm: LLM | None = None) -> Platform:
    settings = settings or Settings.from_env()
    db = make_engine(settings.database_url)

    tools = ToolRegistry()
    register_builtins(tools)
    tools.load_packs(settings.tool_packs)

    skills = SkillRegistry()
    problems = skills.load_dirs(settings.all_skill_dirs())
    problems += skills.validate_tools(tools.names())
    for problem in problems:
        log.warning(problem)

    policy = load_policy(settings.governance_file)
    knowledge = Knowledge(db, settings.behavioral_ttl_days, settings.web_ttl_days)
    client = llm or LiteLLMClient(settings.llm_model, temperature=settings.llm_temperature,
                                  max_tokens=settings.llm_max_tokens)
    engine = Engine(settings=settings, llm=client, skills=skills,
                    tools=tools, policy=policy, knowledge=knowledge, db=db)
    watches = WatchService(db, engine, settings.notify_webhook_url)

    # Runs left "running" past their timeout belong to a dead process; mark them so callers stop waiting.
    stale_before = now() - 2 * settings.run_timeout_seconds
    with db.begin() as conn:
        conn.execute(update(runs).where(runs.c.status == "running", runs.c.created_at < stale_before)
                     .values(status="failed", finished_at=now(),
                             result_json='{"status": "failed", "error": "Server restarted during this run."}'))

    return Platform(settings, db, tools, skills, policy, knowledge, engine, watches, problems)
