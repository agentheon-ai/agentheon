"""A tool pack used by the tests: a tiny 'document lookup' with a known source id."""

from agentheon.tools import ToolRegistry, ToolResult

DOCS = {"pricing": "The Pro plan costs 20 per month.", "limits": "Free plan: 100 requests per day."}


def lookup(topic: str) -> ToolResult:
    return ToolResult(DOCS.get(topic, "No document."), [f"doc://{topic}"] if topic in DOCS else [])


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "lookup",
        "Look up an internal document by topic.",
        {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]},
        refresh_days=14,
    )(lookup)
