"""Tool registry. A tool is a Python function plus a name, description and JSON-schema parameters.

Teams add tools with a "tool pack": any importable module exposing `register(registry)`,
listed in TOOL_PACKS. See examples/tool_packs/.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class ToolError(RuntimeError):
    """Raise this from a tool for an error message the agent should see and recover from."""


@dataclass
class ToolResult:
    content: str
    sources: list[str] = field(default_factory=list)  # URLs or record ids the content came from


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    # How often the data behind this tool changes. Learnings from it expire after this many days.
    # None means "web/general" and uses WEB_TTL_DAYS.
    refresh_days: int | None = None
    max_output_chars: int = 8000

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    async def call(self, args: dict) -> ToolResult:
        if inspect.iscoroutinefunction(self.func):
            raw = await self.func(**args)
        else:
            raw = await asyncio.to_thread(self.func, **args)
        result = _coerce(raw)
        if len(result.content) > self.max_output_chars:
            result.content = result.content[: self.max_output_chars] + "\n...[truncated]"
        return result


def _coerce(raw: Any) -> ToolResult:
    if isinstance(raw, ToolResult):
        return raw
    if isinstance(raw, str):
        return ToolResult(raw)
    if isinstance(raw, dict) and "content" in raw:
        return ToolResult(str(raw["content"]), [str(s) for s in raw.get("sources", [])])
    return ToolResult(json.dumps(raw, default=str, ensure_ascii=False))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def tool(self, name: str, description: str, parameters: dict | None = None, refresh_days: int | None = None):
        """Decorator form: @registry.tool("name", "what it does", {...json schema...})."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register(
                Tool(
                    name=name,
                    description=description,
                    func=func,
                    parameters=parameters or {"type": "object", "properties": {}},
                    refresh_days=refresh_days,
                )
            )
            return func

        return decorator

    def load_packs(self, modules: list[str]) -> None:
        # Let packs in the project folder import when run via the `agentheon` script.
        if modules and os.getcwd() not in sys.path:
            sys.path.insert(0, os.getcwd())
        for module_name in modules:
            module = importlib.import_module(module_name)
            if not hasattr(module, "register"):
                raise ValueError(f"Tool pack {module_name!r} has no register(registry) function")
            module.register(self)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> set[str]:
        return set(self._tools)

    def specs(self, names: list[str]) -> list[dict]:
        return [self._tools[n].spec() for n in names if n in self._tools]
