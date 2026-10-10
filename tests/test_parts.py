"""Unit tests for the small building blocks."""

import time
from pathlib import Path

import pytest

from agentheon.builtin_tools import calculator
from agentheon.config import _api_keys
from agentheon.governance import PolicyError, ground_sources, load_policy
from agentheon.knowledge import DAY, Knowledge, redact
from agentheon.llm import LLMOutputError, parse_json
from agentheon.skills import SkillError, parse_skill
from agentheon.tools import ToolError

GOOD = """---
name: my_skill
description: Does a thing.
tools: [calculator]
required_inputs: [x]
---
Do the thing.
"""


def test_parse_skill():
    skill = parse_skill(GOOD)
    assert skill.name == "my_skill"
    assert skill.tools == ["calculator"]
    assert skill.required_inputs == ["x"]
    assert skill.instructions == "Do the thing."
    assert skill.card()["description"] == "Does a thing."


@pytest.mark.parametrize("text", [
    "no front matter",
    "---\nname: Bad Name\ndescription: x\n---\nbody",
    "---\nname: ok_name\n---\nbody",
    "---\nname: ok_name\ndescription: x\n---\n   ",
    "---\nname: ok_name\ndescription: x\ntools: calculator\n---\nbody",
])
def test_parse_skill_rejects_bad_manifests(text):
    with pytest.raises(SkillError):
        parse_skill(text)


def test_builtin_skills_are_valid():
    folder = Path(__file__).parents[1] / "agentheon" / "builtin_skills"
    for skill_file in folder.glob("*/SKILL.md"):
        parse_skill(skill_file.read_text())


def test_calculator_is_safe():
    assert calculator("(2 + 3) * 4") == "20"
    assert calculator("10 / 4") == "2.5"
    for bad in ["__import__('os')", "a + 1", "2 ** 1000", "1/0"]:
        with pytest.raises(ToolError):
            calculator(bad)


def test_parse_json_handles_fences_and_chatter():
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('prefix {"b": {"c": 2}} suffix') == {"b": {"c": 2}}
    with pytest.raises(LLMOutputError):
        parse_json("no json here")


def test_policy_rules(tmp_path):
    path = tmp_path / "g.yaml"
    path.write_text("""
definitions: {churn: Customers lost in the period divided by customers at the start}
rules:
  - {id: no_advice, type: forbid, pattern: "(?i)you should buy", message: No advice.}
  - {id: year, type: require, pattern: "20\\\\d\\\\d", message: State the year., skills: [docs]}
  - {id: cite, type: require_sources, message: Cite.}
""")
    policy = load_policy(path)
    assert "churn" in policy.prompt_block()
    assert policy.check("In 2025 it rose.", ["doc://x"], "docs") == []
    violations = policy.check("You should buy it.", [], "docs")
    assert {v.split("]")[0] for v in violations} == {"[no_advice", "[year", "[cite"}
    assert policy.check("Fine.", ["s"], "other_skill") == []  # 'year' only applies to docs


def test_policy_rejects_bad_rules(tmp_path):
    path = tmp_path / "g.yaml"
    path.write_text("rules:\n  - {id: x, type: forbid}\n")
    with pytest.raises(PolicyError):
        load_policy(path)
    assert load_policy(tmp_path / "missing.yaml").rules == []


def test_grounding_drops_unseen_sources():
    kept, dropped = ground_sources(["a", "b", "a"], ["a"])
    assert kept == ["a"] and dropped == ["b"]


def test_redact_pii_but_keep_numbers():
    text = redact("Mail jo@example.com or call 555-123-4567. Revenue in 2024 2025 was 1,200.")
    assert "jo@example.com" not in text and "555-123-4567" not in text
    assert "2024 2025" in text and "1,200" in text


def test_knowledge_ttl_recall_and_expiry(tmp_path):
    from agentheon.db import make_engine

    knowledge = Knowledge(make_engine(f"sqlite:///{tmp_path}/k.db"), behavioral_ttl_days=90, web_ttl_days=30)
    assert knowledge.ttl_days("behavioral", [14]) == 90
    assert knowledge.ttl_days("data", [14, None, 7]) == 7
    assert knowledge.ttl_days("data", [None]) == 30

    knowledge.remember("Pro plan pricing is monthly", "data", skill="docs", user_id=None, ttl_days=14)
    knowledge.remember("Alice prefers tables for pricing", "behavioral", skill=None, user_id="alice", ttl_days=90)
    assert knowledge.recall("what is pro pricing", "bob") == ["Pro plan pricing is monthly"]
    assert len(knowledge.recall("pricing", "alice")) == 2

    knowledge.remember("old pricing note", "data", skill=None, user_id=None, ttl_days=-1)
    assert "old pricing note" not in knowledge.recall("pricing note", "bob")
    assert knowledge.purge_expired() == 1


def test_api_keys_parsing():
    assert _api_keys("k1:alice:admin,k2:bot") == {"k1": ("alice", "admin"), "k2": ("bot", "user")}
    with pytest.raises(ValueError):
        _api_keys("k1:alice:root")


def test_day_constant():
    assert DAY == 86400 and time.time() > 0
