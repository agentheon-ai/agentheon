"""Skills are folders with a SKILL.md: YAML front matter (the skill card) + markdown instructions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class SkillError(ValueError):
    pass


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    when_to_use: str = ""
    version: str = "0.1.0"
    tools: list[str] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    max_steps: int | None = None
    require_sources: bool = False
    path: Path | None = None

    def card(self) -> dict:
        """What the planner sees when choosing skills."""
        return {
            "name": self.name,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "required_inputs": self.required_inputs,
        }


def parse_skill(text: str, path: Path | None = None) -> Skill:
    where = f" in {path}" if path else ""
    if not text.startswith("---"):
        raise SkillError(f"SKILL.md must start with '---' front matter{where}")
    try:
        _, front, body = text.split("---", 2)
    except ValueError as exc:
        raise SkillError(f"SKILL.md front matter is not closed with '---'{where}") from exc
    meta = yaml.safe_load(front) or {}
    if not isinstance(meta, dict):
        raise SkillError(f"Front matter must be a mapping{where}")

    name = str(meta.get("name", ""))
    if not NAME_RE.match(name):
        raise SkillError(f"Skill name {name!r} must be lowercase letters, digits or _{where}")
    if not meta.get("description"):
        raise SkillError(f"Skill {name!r} needs a description{where}")
    if not body.strip():
        raise SkillError(f"Skill {name!r} needs instructions below the front matter{where}")

    def as_list(key: str) -> list[str]:
        value = meta.get(key) or []
        if not isinstance(value, list):
            raise SkillError(f"{key} must be a list in skill {name!r}{where}")
        return [str(v) for v in value]

    max_steps = meta.get("max_steps")
    return Skill(
        name=name,
        description=str(meta["description"]).strip(),
        when_to_use=str(meta.get("when_to_use", "")).strip(),
        version=str(meta.get("version", "0.1.0")),
        tools=as_list("tools"),
        required_inputs=as_list("required_inputs"),
        max_steps=int(max_steps) if max_steps else None,
        require_sources=bool(meta.get("require_sources", False)),
        instructions=body.strip(),
        path=path,
    )


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def add(self, skill: Skill) -> None:
        self._skills[skill.name] = skill  # later directories override earlier ones (e.g. builtins)

    def load_dirs(self, dirs: list[Path]) -> list[str]:
        """Load every */SKILL.md. Returns error messages instead of crashing on one bad skill."""
        errors: list[str] = []
        for directory in dirs:
            if not directory.is_dir():
                continue
            for skill_file in sorted(directory.glob("*/SKILL.md")):
                try:
                    self.add(parse_skill(skill_file.read_text(encoding="utf-8"), skill_file))
                except SkillError as exc:
                    errors.append(str(exc))
        return errors

    def validate_tools(self, known_tools: set[str]) -> list[str]:
        return [
            f"Skill {s.name!r} uses unknown tool {t!r}"
            for s in self._skills.values()
            for t in s.tools
            if t not in known_tools
        ]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def names(self) -> list[str]:
        return [s.name for s in self.all()]
