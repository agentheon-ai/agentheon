import json

import pytest

from agentheon.app import build
from agentheon.config import Settings

DOCS_SKILL = """---
name: docs
description: Answers questions about plans and pricing from internal documents.
when_to_use: Pricing, plans and limits.
tools: [lookup]
required_inputs: [topic]
require_sources: true
max_steps: 5
---
Look up the topic, then answer and cite the doc.
"""


class ScriptedLLM:
    """A fake model that replays scripted replies in order and records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    async def complete(self, messages, *, json_mode=False):
        self.calls.append(list(messages))  # snapshot: the engine keeps appending to the list
        if not self.replies:
            raise AssertionError("LLM was called more times than scripted")
        reply = self.replies.pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)

    def add(self, *replies):
        self.replies.extend(replies)


@pytest.fixture
def make_platform(tmp_path):
    def _make(*replies, governance: str | None = None, **overrides):
        skills_dir = tmp_path / "skills"
        (skills_dir / "docs").mkdir(parents=True, exist_ok=True)
        (skills_dir / "docs" / "SKILL.md").write_text(DOCS_SKILL)
        gov = tmp_path / "governance.yaml"
        if governance:
            gov.write_text(governance)
        options = dict(
            database_url=f"sqlite:///{tmp_path}/test.db",
            skills_dirs=[skills_dir],
            governance_file=gov,
            tool_packs=["tests.fake_pack"],
            learning_enabled=False,
            run_scheduler=False,
            auth_dev_mode=True,
        )
        options.update(overrides)
        llm = ScriptedLLM(*replies)
        return build(Settings(**options), llm=llm), llm

    return _make
