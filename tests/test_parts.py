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


async def test_litellm_client_only_sends_temperature_when_asked(monkeypatch):
    """Claude Sonnet 5, Claude Opus 5 and the Opus 4.7+ family reject a non-default temperature
    with a 400, and litellm's drop_params cannot help: temperature is valid for the provider in
    general. max_tokens must always be sent, because on thinking models it covers the thinking
    and the reply together and a truncated reply is not valid JSON."""
    import sys
    import types

    from agentheon.llm import LiteLLMClient

    captured: dict = {}
    fake = types.ModuleType("litellm")
    fake.drop_params = False

    async def acompletion(**kwargs):
        captured.clear()
        captured.update(kwargs)
        message = types.SimpleNamespace(content='{"ok": true}')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    fake.acompletion = acompletion
    monkeypatch.setitem(sys.modules, "litellm", fake)

    await LiteLLMClient("anthropic/claude-sonnet-5").complete([{"role": "user", "content": "hi"}])
    assert "temperature" not in captured
    assert captured["max_tokens"] == 8192
    assert "response_format" not in captured

    await LiteLLMClient("gpt-4o-mini", temperature=0.2, max_tokens=256).complete([], json_mode=True)
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 256
    assert captured["response_format"] == {"type": "json_object"}


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


def test_policy_rejects_patterns_that_could_never_match(tmp_path):
    """A double-quoted "\\b" in YAML is a backspace, not a word boundary: the rule would never fire."""
    path = tmp_path / "g.yaml"
    path.write_text(r'rules: [{id: wb, type: forbid, pattern: "\byou should\b", message: No advice.}]')
    with pytest.raises(PolicyError, match="control character"):
        load_policy(path)


@pytest.mark.parametrize("pattern", [
    r'"(?i)\\byou should\\b"',  # escaped backslash in a double-quoted scalar
    r"'(?i)\byou should\b'",    # or a single-quoted scalar, which does not unescape
])
def test_policy_accepts_correctly_escaped_word_boundaries(tmp_path, pattern):
    path = tmp_path / "g.yaml"
    path.write_text(f"rules: [{{id: wb, type: forbid, pattern: {pattern}, message: No advice.}}]")
    policy = load_policy(path)
    assert policy.check("You should buy it.", [], "docs") == ["[wb] No advice."]
    assert policy.check("Perhaps you shouldn't.", [], "docs") == []


def test_policy_fails_closed_when_the_file_is_a_directory(tmp_path):
    """Docker creates a directory when it bind-mounts a file that does not exist yet. Never run
    with zero rules because of that: a governed platform silently enforcing nothing is the worst
    possible outcome."""
    as_directory = tmp_path / "governance.yaml"
    as_directory.mkdir()
    with pytest.raises(PolicyError, match="not a regular file"):
        load_policy(as_directory)


def test_describe_failure_names_the_real_cause():
    """A billing problem, an unknown model and a revoked key need three different fixes, so the
    provider's own sentence is what belongs in the error."""
    from agentheon.engine import describe_failure

    class BadRequestError(Exception):
        pass

    BadRequestError.__module__ = "litellm.exceptions"
    provider = BadRequestError(
        'litellm.BadRequestError: AnthropicException - {"type":"error","error":'
        '{"type":"invalid_request_error","message":"Your credit balance is too low."},'
        '"request_id":"req_1"} LiteLLM Retried: 2 times'
    )
    message = describe_failure(provider)
    assert "Your credit balance is too low." in message
    assert "LiteLLM Retried" not in message  # litellm's own retry count is noise
    assert "API key" not in message  # the old message blamed the key for every failure

    # Our own bugs stay vague on purpose: the person asking cannot act on a traceback.
    assert describe_failure(ValueError("boom")) == "ValueError during the run. Details are in the server log."

    # Provider text is stored and returned, so anything key-shaped is scrubbed on the way out.
    leaky = BadRequestError("Incorrect API key provided: sk-proj-abc123def456ghi789")
    scrubbed = describe_failure(leaky)
    assert "sk-proj-abc123def456ghi789" not in scrubbed
    assert "[redacted]" in scrubbed


def test_retry_feedback_tells_the_model_how_to_fix_a_missing_source():
    """Smaller models answer well but leave the `sources` array empty. Repeating the violation
    back at them just burns the one regeneration, so the retry names the field and lists the
    sources the tools actually returned."""
    from agentheon.engine import retry_feedback

    message = retry_feedback(
        ["[cite_sources] Cite at least one source returned by a tool."],
        cited=[],
        observed=["https://sqlite.org/changes.html", "https://sqlite.org/changes.html",
                  "https://example.com/b"],
    )
    assert '"sources"' in message                      # names the field, not just the rule
    assert "https://sqlite.org/changes.html" in message
    assert "https://example.com/b" in message
    assert message.count("https://sqlite.org/changes.html") == 1   # de-duplicated
    assert "does not count" in message                 # answer-text URLs are not citations

    # nothing to copy from yet -> say so instead of listing an empty set
    empty = retry_feedback(["[cite_sources] Cite a source."], cited=[], observed=[])
    assert "No tool has returned a source yet" in empty
    assert '"sources"' not in empty

    # a violation unrelated to sources stays terse
    other = retry_feedback(["[no_absolute_claims] Avoid absolute claims."], cited=[], observed=["x"])
    assert "sources" not in other.lower()
    assert "Fix every point" in other


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
