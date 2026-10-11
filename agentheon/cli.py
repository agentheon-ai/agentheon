"""Command line: agentheon serve | ask | check | new-skill | mcp."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

SKILL_TEMPLATE = """---
name: {name}
version: 0.1.0
description: One sentence on what this skill does.
when_to_use: The kinds of questions that should be routed here.
tools: [web_search, fetch_page]
required_inputs: []
require_sources: true
max_steps: 8
---
## Instructions

Describe, step by step, how to answer questions for this skill:
1. Which tools to call first and why.
2. What to check before answering.
3. How to format the answer (tables, bullet points, units).
"""


def cmd_serve(args) -> None:
    import uvicorn

    from .api import create_app

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True)


def cmd_ask(args) -> None:
    from .app import build

    platform = build()

    async def emit(event: str, data: dict) -> None:
        if args.verbose and event != "result":
            print(f"  · {event}: {json.dumps(data)[:200]}", file=sys.stderr)

    outcome = asyncio.run(platform.engine.run(
        args.question, user_id="cli", skills=args.skill or None, private=args.private, emit=emit))
    if args.json:
        print(json.dumps(outcome, indent=2, default=str))
        return
    status = outcome["status"]
    if status == "succeeded":
        print(outcome["answer"])
        if outcome["provenance"]["sources"]:
            print("\nSources:")
            for source in outcome["provenance"]["sources"]:
                print(f"  - {source}")
    elif status == "needs_user_input":
        print(f"? {outcome['question']}")
    else:
        print(f"Failed: {outcome['error']}", file=sys.stderr)
        sys.exit(1)


def cmd_check(_) -> None:
    from .app import build

    platform = build()
    print(f"Skills ({len(platform.skills.all())}): {', '.join(platform.skills.names()) or 'none'}")
    print(f"Tools ({len(platform.tools.names())}): {', '.join(sorted(platform.tools.names()))}")
    print(f"Governance: {len(platform.policy.definitions)} definitions, {len(platform.policy.rules)} rules")
    if platform.problems:
        print("\nProblems:")
        for problem in platform.problems:
            print(f"  - {problem}")
        sys.exit(1)
    print("\nAll good.")


def cmd_new_skill(args) -> None:
    from .skills import NAME_RE

    if not NAME_RE.match(args.name):
        sys.exit("Skill names use lowercase letters, digits and underscores, e.g. release_tracker")
    folder = Path(args.dir) / args.name
    if folder.exists():
        sys.exit(f"{folder} already exists")
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL_TEMPLATE.format(name=args.name))
    print(f"Created {folder / 'SKILL.md'}. Edit it, then run: agentheon check")


def cmd_mcp(_) -> None:
    from .mcp_server import main

    main()


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentheon", description="A governed, skills-based agent platform.")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the web app and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    ask = sub.add_parser("ask", help="Ask one question from the terminal")
    ask.add_argument("question")
    ask.add_argument("--skill", action="append", help="Use this skill (repeatable); skips the planner")
    ask.add_argument("--private", action="store_true", help="Do not read or write learned knowledge")
    ask.add_argument("--json", action="store_true", help="Print the full result as JSON")
    ask.add_argument("-v", "--verbose", action="store_true", help="Show each step")
    ask.set_defaults(func=cmd_ask)

    check = sub.add_parser("check", help="Validate skills, tools and governance")
    check.set_defaults(func=cmd_check)

    new_skill = sub.add_parser("new-skill", help="Create a SKILL.md from the template")
    new_skill.add_argument("name")
    new_skill.add_argument("--dir", default="skills")
    new_skill.set_defaults(func=cmd_new_skill)

    mcp = sub.add_parser("mcp", help="Run as an MCP server over stdio")
    mcp.set_defaults(func=cmd_mcp)

    args = parser.parse_args()
    from .governance import PolicyError

    try:
        args.func(args)
    except PolicyError as exc:
        sys.exit(f"Governance error: {exc}")
    except ModuleNotFoundError as exc:
        sys.exit(f"Could not import a tool pack from TOOL_PACKS: {exc}")


if __name__ == "__main__":
    main()
