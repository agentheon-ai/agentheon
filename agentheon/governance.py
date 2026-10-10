"""Governance: definitions shape the prompt; rules are checked deterministically after the answer.

governance.yaml:

    definitions:
      active user: A user with at least one session in the last 28 days.
    rules:
      - id: no_investment_advice
        type: forbid            # answer must NOT match pattern
        pattern: "(?i)\\byou should (buy|sell)\\b"
        message: Do not give investment advice.
      - id: cite_sources
        type: require_sources   # answer must cite at least one source returned by a tool
        skills: [web_research]  # optional: only for these skills
      - id: state_period
        type: require           # answer MUST match pattern
        pattern: "(?i)\\b(19|20)\\d{2}\\b"
        message: State the time period the numbers refer to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RULE_TYPES = {"forbid", "require", "require_sources"}


class PolicyError(ValueError):
    pass


@dataclass
class Rule:
    id: str
    type: str
    pattern: re.Pattern | None = None
    message: str = ""
    skills: list[str] | None = None

    def applies_to(self, skill: str) -> bool:
        return self.skills is None or skill in self.skills


@dataclass
class Policy:
    definitions: dict[str, str] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)

    def prompt_block(self) -> str:
        if not self.definitions and not self.rules:
            return ""
        lines = ["Governed definitions and rules (mandatory):"]
        lines += [f"- Definition of '{term}': {meaning}" for term, meaning in self.definitions.items()]
        lines += [f"- Rule {r.id}: {r.message}" for r in self.rules if r.message]
        return "\n".join(lines)

    def check(self, answer: str, sources: list[str], skill: str) -> list[str]:
        """Return human-readable violations. Empty list means the answer passes."""
        violations = []
        for rule in self.rules:
            if not rule.applies_to(skill):
                continue
            label = f"[{rule.id}] {rule.message}".strip()
            if rule.type == "forbid" and rule.pattern and rule.pattern.search(answer):
                violations.append(label or f"[{rule.id}] forbidden content")
            elif rule.type == "require" and rule.pattern and not rule.pattern.search(answer):
                violations.append(label or f"[{rule.id}] required content missing")
            elif rule.type == "require_sources" and not sources:
                violations.append(label if rule.message else f"[{rule.id}] cite at least one source")
        return violations


def load_policy(path: Path) -> Policy:
    if not path.is_file():
        return Policy()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    definitions = {str(k): str(v) for k, v in (data.get("definitions") or {}).items()}
    rules = []
    for raw in data.get("rules") or []:
        rule_type = raw.get("type")
        if rule_type not in RULE_TYPES:
            raise PolicyError(f"Rule {raw.get('id')!r}: type must be one of {sorted(RULE_TYPES)}")
        pattern = raw.get("pattern")
        if rule_type in {"forbid", "require"} and not pattern:
            raise PolicyError(f"Rule {raw.get('id')!r}: '{rule_type}' needs a pattern")
        try:
            compiled = re.compile(pattern) if pattern else None
        except re.error as exc:
            raise PolicyError(f"Rule {raw.get('id')!r}: bad pattern: {exc}") from exc
        rules.append(
            Rule(
                id=str(raw.get("id") or f"rule_{len(rules) + 1}"),
                type=rule_type,
                pattern=compiled,
                message=str(raw.get("message", "")),
                skills=raw.get("skills"),
            )
        )
    return Policy(definitions=definitions, rules=rules)


def ground_sources(cited: list[str], observed: list[str]) -> tuple[list[str], list[str]]:
    """Grounding gate: keep only cited sources that a tool actually returned. Returns (kept, dropped)."""
    seen = set(observed)
    kept, dropped = [], []
    for source in dict.fromkeys(cited):  # de-duplicate, keep order
        (kept if source in seen else dropped).append(source)
    return kept, dropped
