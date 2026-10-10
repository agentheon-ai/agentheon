# Architecture

Agentheon is one Python package of about a dozen modules. Each has one job.

| Module | Job |
|---|---|
| `config.py` | Reads settings from environment variables |
| `db.py` | Four tables: `runs` (audit), `learnings`, `watches`, `watch_runs` |
| `llm.py` | The only code that talks to a model (via LiteLLM), plus tolerant JSON parsing |
| `skills.py` | Parses and validates `SKILL.md` files |
| `tools.py` | Tool registry and tool packs |
| `builtin_tools.py` | `calculator`, `current_time`, `web_search`, `fetch_page` |
| `governance.py` | Definitions, deterministic rules, grounding |
| `knowledge.py` | Learned notes: redaction, expiry, recall |
| `engine.py` | The harness: plan → skill loops → gates → synthesize → learn → audit |
| `watches.py` | Scheduled re-asks, change judging, notifications |
| `app.py` | Builds the platform from settings |
| `api.py` | FastAPI routes and the web page |
| `mcp_server.py` | MCP server (`ask`, `list_skills`) |
| `cli.py` | `serve`, `ask`, `check`, `new-skill`, `mcp` |

## Design choices

**Skills are data.** A skill is front matter plus instructions. The planner only ever sees the
front matter (the "card"), so cards should say clearly when a skill applies.

**One JSON protocol for every model.** Skills reply with `{"action": "tool" | "answer" | "ask"}`
instead of provider-specific function calling. That keeps every LiteLLM model usable, including
small local ones, and makes the engine testable with a scripted fake model.

**Governance is code.** Rules are regexes and source checks evaluated after the model answers, so
they cannot be talked around. The model gets exactly one chance to fix a violation; after that
the run fails closed.

**Grounding is mechanical.** Tools return `sources`. An answer may only cite sources seen in its
own tool results; anything else is dropped and reported in `gates.<skill>.dropped_sources`.

**Everything expires.** Learned notes carry `expires_at`, set from the freshness of the data they
came from. Recall filters expired notes; the scheduler purges them.

**Small concurrency model.** Requests run on the event loop. Skills chosen for one question run in
parallel with `asyncio.gather`. Background runs (`submit`, streaming) are recorded before they
start, so they can always be polled, and they finish even if the client disconnects.

## Data model

```
runs        id, user_id, question, status, result_json, created_at, finished_at
learnings   id, text, kind (data|behavioral), skill, user_id (null = shared), created_at, expires_at
watches     id, owner, name, question, skills, interval_minutes, threshold, history_window,
            max_runs, runs_done, active, next_run_at, created_at
watch_runs  id, watch_id, run_id, status, answer, answer_hash, severity, summary, notified, created_at
```

Tables are created on startup. For schema changes after v0.1, add Alembic migrations.
