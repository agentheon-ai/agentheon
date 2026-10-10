"""Expose Agentheon as an MCP server so other agents (Claude Desktop, IDEs, other platforms) can use it.

Run: agentheon mcp   (stdio transport). Requires: pip install 'agentheon[mcp]'.
Runs as the user named by AGENTHEON_MCP_USER (default "mcp").
"""

from __future__ import annotations

import os

from .app import Platform, build


def create_server(platform: Platform | None = None):
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP  # mcp >= 2.0
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP  # mcp 1.x
        except ImportError as exc:
            raise SystemExit("MCP support is not installed. Run: pip install 'agentheon[mcp]'") from exc

    platform = platform or build()
    user_id = os.environ.get("AGENTHEON_MCP_USER", "mcp")
    server = FastMCP("agentheon")

    @server.tool()
    async def ask(question: str, skills: list[str] | None = None, private: bool = False) -> dict:
        """Ask Agentheon a question. Returns status (succeeded | needs_user_input | failed), the answer,
        a clarifying question if one is needed, and provenance (skills, tools, sources)."""
        outcome = await platform.engine.run(question, user_id=user_id, skills=skills, private=private)
        outcome.pop("trace", None)
        return outcome

    @server.tool()
    def list_skills() -> list[dict]:
        """List the skills Agentheon can use, with what each is for."""
        return [s.card() for s in platform.skills.all()]

    return server


def main() -> None:
    create_server().run()
