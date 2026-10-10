"""Example tool pack: public GitHub releases. Enable with TOOL_PACKS=examples.tool_packs.github_releases

A tool pack is any importable module with a register(registry) function. That's the whole contract.
"""

from __future__ import annotations

import os

import httpx

from agentheon.tools import ToolError, ToolRegistry, ToolResult


async def github_releases(repo: str, limit: int = 5) -> ToolResult:
    if repo.count("/") != 1:
        raise ToolError("repo must look like 'owner/name', e.g. 'qdrant/qdrant'")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "agentheon"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/releases"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params={"per_page": min(int(limit), 20)}, headers=headers)
    if response.status_code == 404:
        raise ToolError(f"Repository {repo} not found")
    if response.status_code >= 400:
        raise ToolError(f"GitHub returned HTTP {response.status_code}")
    releases = response.json()
    lines = [
        f"- {r.get('tag_name')} ({(r.get('published_at') or '')[:10]}): {r.get('name') or ''}\n"
        f"  {(r.get('body') or '').strip()[:600]}"
        for r in releases
    ]
    return ToolResult("\n".join(lines) or "No releases.", [r["html_url"] for r in releases if r.get("html_url")])


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "github_releases",
        "List the latest releases of a public GitHub repository (tag, date, notes).",
        {
            "type": "object",
            "properties": {"repo": {"type": "string", "description": "owner/name"}, "limit": {"type": "integer"}},
            "required": ["repo"],
        },
        refresh_days=1,  # releases change often; learnings from this tool expire after a day
    )(github_releases)
