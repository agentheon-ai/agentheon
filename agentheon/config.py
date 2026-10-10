"""All settings come from environment variables (or a .env file). No other config system."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtin_skills"


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _api_keys(value: str | None) -> dict[str, tuple[str, str]]:
    """API_KEYS="key1:alice:admin,key2:reporting-bot:user" -> {key: (user, role)}."""
    keys: dict[str, tuple[str, str]] = {}
    for entry in _list(value):
        parts = entry.split(":")
        if len(parts) < 2:
            raise ValueError(f"API_KEYS entry must be key:user[:role], got {entry!r}")
        role = parts[2] if len(parts) > 2 else "user"
        if role not in {"user", "admin"}:
            raise ValueError(f"API_KEYS role must be 'user' or 'admin', got {role!r}")
        keys[parts[0]] = (parts[1], role)
    return keys


@dataclass(frozen=True)
class Settings:
    llm_model: str = "gpt-4o-mini"
    database_url: str = "sqlite:///agentheon.db"
    skills_dirs: list[Path] = field(default_factory=lambda: [Path("skills")])
    include_builtin_skills: bool = True
    governance_file: Path = Path("governance.yaml")
    tool_packs: list[str] = field(default_factory=list)
    api_keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    auth_dev_mode: bool = False
    run_scheduler: bool = True
    watch_poll_seconds: int = 30
    notify_webhook_url: str | None = None
    max_steps: int = 8
    run_timeout_seconds: int = 180
    max_question_chars: int = 8000
    learning_enabled: bool = True
    behavioral_ttl_days: int = 90
    web_ttl_days: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        _load_dotenv()
        env = os.environ.get
        return cls(
            llm_model=env("LLM_MODEL", cls.llm_model),
            database_url=env("DATABASE_URL", cls.database_url),
            skills_dirs=[Path(p) for p in _list(env("SKILLS_DIRS", "skills"))],
            include_builtin_skills=_bool(env("INCLUDE_BUILTIN_SKILLS"), True),
            governance_file=Path(env("GOVERNANCE_FILE", "governance.yaml")),
            tool_packs=_list(env("TOOL_PACKS")),
            api_keys=_api_keys(env("API_KEYS")),
            auth_dev_mode=_bool(env("AUTH_DEV_MODE"), False),
            run_scheduler=_bool(env("RUN_SCHEDULER"), True),
            watch_poll_seconds=int(env("WATCH_POLL_SECONDS", "30")),
            notify_webhook_url=env("NOTIFY_WEBHOOK_URL") or None,
            max_steps=int(env("MAX_STEPS", "8")),
            run_timeout_seconds=int(env("RUN_TIMEOUT_SECONDS", "180")),
            max_question_chars=int(env("MAX_QUESTION_CHARS", "8000")),
            learning_enabled=_bool(env("LEARNING_ENABLED"), True),
            behavioral_ttl_days=int(env("BEHAVIORAL_TTL_DAYS", "90")),
            web_ttl_days=int(env("WEB_TTL_DAYS", "30")),
        )

    def all_skill_dirs(self) -> list[Path]:
        dirs = [BUILTIN_SKILLS_DIR] if self.include_builtin_skills else []
        return dirs + list(self.skills_dirs)
