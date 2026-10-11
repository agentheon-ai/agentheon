r"""Governance: definitions shape the prompt; rules are checked deterministically after the answer.

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

Escaping, because it is easy to get wrong and fails quietly: a double-quoted YAML scalar
unescapes backslashes, so "\b" reaches the regex as a backspace and the rule then never matches.
Write "\\b" as above, or single-quote the whole pattern ('\byou should\b'). Patterns containing
control characters are rejected when the file loads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RULE_TYPES = {"forbid", "require", "require_sources"}

# YAML double-quoted scalars interpret backslash escapes, so "\b" becomes a backspace character
# rather than a regex word boundary. The pattern still compiles and a 'forbid' rule then silently
# never matches, which means governance quietly passes everything. Reject those characters instead.
# Tab, newline and carriage return are left alone: they are legitimate inside a pattern.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


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
    # A path that exists but is not a regular file is always a misconfiguration, and silently
    # running with no rules is the worst possible outcome for a governed platform. Fail closed.
    # Docker creates a directory when you bind-mount a file that does not exist yet, which is the
    # usual way to land here: copy governance.example.yaml to governance.yaml before composing up.
    if path.exists() and not path.is_file():
        raise PolicyError(
            f"Governance file {path} exists but is not a regular file (it is a directory). "
            "If you are using Docker, the bind mount for it pointed at a missing file, so Docker "
            "created a directory. Remove it and run: cp governance.example.yaml governance.yaml"
        )
    if not path.is_file():
        return Policy()  # governance is optional; no file means no rules
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
        if pattern and (found := _CONTROL_CHARS.search(str(pattern))):
            raise PolicyError(
                rf"Rule {raw.get('id')!r}: pattern contains the control character "
                rf"\x{ord(found.group()):02x}, so it would never match real text. YAML turns \b "
                rf"inside double quotes into a backspace: write \\b for a word boundary, or "
                rf"single-quote the pattern instead ('\byou should\b')."
            )
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
