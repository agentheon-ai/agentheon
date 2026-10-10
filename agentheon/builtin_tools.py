"""Tools that ship with Agentheon. Deliberately few: everything domain-specific belongs in a tool pack."""

from __future__ import annotations

import ast
import html
import ipaddress
import operator
import re
import socket
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from .tools import ToolError, ToolRegistry, ToolResult

# ---- calculator: safe arithmetic, no eval() -------------------------------------------------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ToolError("Exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_evaluate(node.operand))
    raise ToolError("Only numbers and + - * / // % ** ( ) are allowed")


def calculator(expression: str) -> str:
    if len(expression) > 200:
        raise ToolError("Expression too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Invalid expression: {exc.msg}") from exc
    try:
        return str(_evaluate(tree))
    except ZeroDivisionError as exc:
        raise ToolError("Division by zero") from exc


# ---- time ------------------------------------------------------------------------------------


def current_time() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---- web -------------------------------------------------------------------------------------


def _check_public_url(url: str) -> None:
    """Block requests to private networks (basic SSRF protection)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolError("Only http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ToolError(f"Cannot resolve host {parsed.hostname}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ToolError("URLs pointing to private or local addresses are not allowed")


_TAGS = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>|<[^>]+>", re.DOTALL | re.IGNORECASE)


async def fetch_page(url: str) -> ToolResult:
    _check_public_url(url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers={"User-Agent": "agentheon"}) as c:
        try:
            response = await c.get(url)
        except httpx.HTTPError as exc:
            raise ToolError(f"Fetch failed: {exc}") from exc
    if response.status_code >= 400:
        raise ToolError(f"Fetch failed with HTTP {response.status_code}")
    text = html.unescape(_TAGS.sub(" ", response.text))
    text = re.sub(r"\s+", " ", text).strip()
    return ToolResult(text, [str(response.url)])


def web_search(query: str, max_results: int = 5) -> ToolResult:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise ToolError("Web search is not installed. Run: pip install 'agentheon[search]'") from exc
    try:
        hits = list(DDGS().text(query, max_results=min(int(max_results), 10)))
    except Exception as exc:  # the search backend raises many types
        raise ToolError(f"Search failed: {exc}") from exc
    lines = [f"- {h.get('title')} ({h.get('href')}): {h.get('body')}" for h in hits]
    return ToolResult("\n".join(lines) or "No results.", [h["href"] for h in hits if h.get("href")])


def register_builtins(registry: ToolRegistry) -> None:
    registry.tool(
        "calculator",
        "Evaluate an arithmetic expression exactly. Use it for any math instead of computing in your head.",
        {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
    )(calculator)
    registry.tool("current_time", "Current date and time in UTC (ISO 8601).")(current_time)
    registry.tool(
        "web_search",
        "Search the public web. Returns titles, URLs and snippets.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
            "required": ["query"],
        },
    )(web_search)
    registry.tool(
        "fetch_page",
        "Download a public web page and return its text.",
        {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    )(fetch_page)
