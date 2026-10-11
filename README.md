# Agentheon

[![CI](https://github.com/agentheon-ai/agentheon/actions/workflows/ci.yml/badge.svg)](https://github.com/agentheon-ai/agentheon/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentheon.svg)](https://pypi.org/project/agentheon/)
[![Python](https://img.shields.io/pypi/pyversions/agentheon.svg)](https://pypi.org/project/agentheon/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**A small, governed, skills-based agent platform.** Write a skill as a markdown file, plug in your
tools, and get an assistant whose answers are sourced, checked against your rules and fully
auditable, from a web page, a REST API or as an MCP server.

Agentheon ships with **no domain data**. You bring the domain; it brings the harness:

| You bring | Agentheon brings |
|---|---|
| **Skills**: one `SKILL.md` per capability, no code | A planner that routes each question to the right skills |
| **Tools**: Python functions in a "tool pack" | A tool loop per skill, with step and time budgets |
| **Rules**: definitions and checks in `governance.yaml` | A compliance gate: one retry, then **fail closed** |
| | A grounding gate: only sources a tool actually returned can be cited |
| | Safe learning: PII redacted, every note expires |
| | Watches: re-ask on a schedule, alert only on real change |
| | Web UI, REST (sync, streaming, async jobs), MCP server, audit trail |

One Python process, SQLite by default, no other services required.

---

## Quick start

```bash
git clone https://github.com/agentheon-ai/agentheon && cd agentheon
pip install -e ".[search,mcp]"
cp .env.example .env               # set LLM_MODEL and its API key (or use a local Ollama model)
cp governance.example.yaml governance.yaml
agentheon check                    # validates skills, tools and rules
agentheon serve                    # http://127.0.0.1:8000
```

`agentheon check` prints how many skills, tools and rules loaded. If it reports `0 rules` when you
expected some, nothing is being enforced — check `GOVERNANCE_FILE`.

To try it without API keys, set `AUTH_DEV_MODE=true` in `.env` for keyless admin access on your
laptop. Set `API_KEYS` and turn it back off before putting Agentheon on a network.

From the terminal:

```bash
agentheon ask "What changed in the last two Qdrant releases?" -v
agentheon ask "@calculator 18% of 2,340, compounded over 3 years"
```

Any model [LiteLLM](https://docs.litellm.ai/docs/providers) supports works: OpenAI, Anthropic,
Azure, Bedrock, Gemini, Ollama and more. Just change `LLM_MODEL`.

---

## How a question is answered

```
question ─► guard ─► planner ─► skill loop(s) ─► gates ─► synthesizer ─► learn ─► audit
                     picks       tool → tool →     compliance   merges when      redacted,
                     skills or   … → answer        + grounding  >1 skill         expiring
                     asks back
```

1. **Guard.** Empty or oversized questions are rejected.
2. **Plan.** The planner reads every skill's card and picks up to three, says the question is out of
   scope, or **asks the user** for a missing required input. Tag a skill (`@release_tracker`) to skip it.
3. **Skill loop.** Each skill calls its tools until it can answer, within `max_steps`.
4. **Gates.** The answer is checked against `governance.yaml`. Cited sources not returned by a tool
   are dropped. On a violation the skill gets one regeneration with the reasons; if it fails again,
   the run returns `failed`. A wrong answer is never returned as a right one.
5. **Synthesize.** If several skills answered, their answers are merged and checked again.
6. **Learn.** Up to three short notes are kept for future questions (skipped for private runs).
7. **Audit.** The full result is stored in the `runs` table.

Every consumer gets the same result:

```json
{
  "run_id": "…",
  "status": "succeeded | needs_user_input | failed",
  "answer": "…",
  "question": "asked when status is needs_user_input",
  "error": "set when status is failed",
  "provenance": {"skills": ["…"], "tools": ["…"], "sources": ["…"], "memory_used": 2},
  "gates": {"<skill>": {"compliance": "pass", "violations": [], "regenerated": false, "dropped_sources": []}},
  "trace": [{"skill": "…", "status": "…", "tools": ["…"]}]
}
```

---

## Add your domain

### 1. Write a skill

```bash
agentheon new-skill churn_analyst      # creates skills/churn_analyst/SKILL.md
```

```markdown
---
name: churn_analyst
version: 0.1.0
description: Explains customer churn by plan and month from the warehouse.
when_to_use: Questions about churn, retention or cancellations.
tools: [sql_query, calculator]
required_inputs: [period]
require_sources: true
max_steps: 8
---
## Instructions
1. Query monthly churn for the requested period with `sql_query`.
2. Compute rates with `calculator`, using the governed definition of churn.
3. Answer with a table, then two sentences on what stands out.
```

The front matter is the **skill card** the planner reads; the body is the skill's instructions.

### 2. Add tools with a tool pack

A tool pack is any importable module with a `register(registry)` function:

```python
# my_company/tools.py
from agentheon.tools import ToolError, ToolResult

def sql_query(query: str) -> ToolResult:
    if not query.lstrip().lower().startswith("select"):
        raise ToolError("Only SELECT queries are allowed")   # the agent sees this and can retry
    rows = run_readonly(query)                                # your code
    return ToolResult(content=format_rows(rows), sources=["warehouse://analytics.churn"])

def register(registry):
    registry.tool(
        "sql_query", "Run a read-only SQL query on the analytics warehouse.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        refresh_days=1,          # learnings from this data expire after a day
    )(sql_query)
```

```bash
TOOL_PACKS=my_company.tools
```

Return `sources` so answers can cite them. Tools can be sync or async. See
[`examples/tool_packs/github_releases.py`](examples/tool_packs/github_releases.py).

### 3. Write your rules

```yaml
# governance.yaml
definitions:
  churn: Customers lost in the period divided by customers at the start of the period.
rules:
  - id: no_investment_advice
    type: forbid                  # must not match
    pattern: "(?i)\\byou should (buy|sell)\\b"
    message: Do not give buy or sell recommendations.
  - id: state_period
    type: require                 # must match
    pattern: "(?i)\\b(19|20)\\d{2}\\b"
    message: State the period the numbers refer to.
    skills: [churn_analyst]       # optional scope
  - id: cite
    type: require_sources         # at least one tool-returned source
    message: Cite your sources.
```

Definitions go into every skill prompt. Rules are checked in code, not by the model.

Note the double backslashes. A double-quoted YAML scalar unescapes them, so `"\b"` would reach the
regex as a backspace character and the rule would quietly never match. Write `"\\b"`, or
single-quote the pattern (`'\byou should\b'`). Agentheon rejects patterns containing control
characters at load time rather than letting them pass silently.

---

## Use it from other systems

Authenticate with `Authorization: Bearer <key>` (keys come from `API_KEYS`).

| Endpoint | What it does |
|---|---|
| `POST /api/v1/query` | Ask and wait. Body: `{"question": "…", "skills": ["…"]?, "private": false}` |
| `POST /api/v1/query/stream` | Same, as Server-Sent Events: `started`, `plan`, `tool_call`, `tool_result`, `gate_retry`, `result` |
| `POST /api/v1/query/submit` | Start in the background, returns `run_id` |
| `GET /api/v1/query/result/{run_id}` | Poll a background run |
| `GET /api/v1/runs` | Your recent runs (admins see all) |
| `GET /api/v1/skills` | Installed skills |
| `POST/GET /api/v1/watches`, `…/{id}/run-now`, `/pause`, `/resume`, `DELETE …/{id}` | Watches |
| `GET /api/v1/ready` | Health: database, skills, tools, load problems (no auth) |

Interactive docs: `http://127.0.0.1:8000/docs`.

**MCP.** `agentheon mcp` runs Agentheon as an MCP server over stdio with two tools, `ask` and
`list_skills`. For example, in Claude Desktop's config:

```json
{"mcpServers": {"agentheon": {"command": "agentheon", "args": ["mcp"], "cwd": "/path/to/agentheon"}}}
```

---

## Watches

"Watch this" on any answer (or `POST /api/v1/watches`) re-asks the question every
`interval_minutes`. Every run is kept. When the answer text changes, a model compares it with the
last `history_window` runs and rates the change `none`, `minor`, `notable` or `major`, ignoring
rewording. At or above the watch's `threshold`, Agentheon records a notification and, if
`NOTIFY_WEBHOOK_URL` is set, POSTs it there (works with Slack/Teams incoming webhooks via a small
relay, or any HTTP endpoint).

---

## Learned knowledge

After a successful run the model proposes up to three reusable notes:

- **data** notes (facts from tools) are shared, and expire with the shortest `refresh_days` of the
  tools used, or `WEB_TTL_DAYS`.
- **behavioral** notes (how a user likes answers) stay private to that user and expire after
  `BEHAVIORAL_TTL_DAYS`.

Emails, phone numbers, card and ID numbers are redacted before storage. Notes are recalled into
the prompt as "may be outdated; verify". Set `private: true` on a question to neither read nor
write notes, or `LEARNING_ENABLED=false` to turn learning off.

---

## Configuration

All settings are environment variables; see [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `LLM_MODEL` | `gpt-4o-mini` | Any LiteLLM model string |
| `LLM_MAX_TOKENS` | `8192` | Cap per model reply. On thinking models this covers thinking **and** the reply — too low truncates the JSON |
| `LLM_TEMPERATURE` | empty | Empty means the provider's own default. Claude Sonnet 5, Claude Opus 5 and the Opus 4.7+ family reject a non-default temperature with a 400 |
| `DATABASE_URL` | `sqlite:///agentheon.db` | Postgres: `postgresql+psycopg://…` (install `.[postgres]`) |
| `API_KEYS` | empty | `key:user:role,…` with role `user` or `admin` |
| `AUTH_DEV_MODE` | `false` | Allow keyless access as an admin. Laptop only |
| `SKILLS_DIRS` | `skills` | Comma-separated folders of `*/SKILL.md` |
| `TOOL_PACKS` | empty | Comma-separated importable modules |
| `GOVERNANCE_FILE` | `governance.yaml` | Definitions and rules |
| `MAX_STEPS` / `RUN_TIMEOUT_SECONDS` | `8` / `180` | Budgets per skill / per run |
| `RUN_SCHEDULER` | `true` | Run watches in this process |
| `NOTIFY_WEBHOOK_URL` | empty | Where watch alerts are POSTed |

---

## Deploy

```bash
cp .env.example .env                       # set LLM_MODEL, its API key and API_KEYS
cp governance.example.yaml governance.yaml # compose bind-mounts this file; create it first
docker compose up --build                  # Agentheon + Postgres on http://localhost:8000
```

Create `governance.yaml` before composing up. Compose bind-mounts it, and Docker silently creates a
*directory* in its place if the file is missing — Agentheon now refuses to start in that case
rather than running with no rules.

Set `API_KEYS` and leave `AUTH_DEV_MODE=false`; the compose file passes your `.env` straight
through, and `AUTH_DEV_MODE=true` would expose keyless admin access.

For more than one replica: point every replica at the same Postgres, and set `RUN_SCHEDULER=true`
on exactly one of them. Put Agentheon behind your usual reverse proxy for TLS. If you want SSO,
put an identity-aware proxy in front and issue API keys for machine clients.

---

## What this is not (yet)

Agentheon keeps the moving parts small on purpose. Not included in v0.1:

- Durable workflow engines (runs interrupted by a restart are marked failed; watches resume on schedule)
- Vector search (recall uses keyword overlap, which is enough for short notes)
- Calling MCP servers as tools (Agentheon *is* an MCP server; tool packs cover the client side)
- SSO inside the app, a skill marketplace, or a guided domain wizard

Each can be added without changing how skills, tools or rules are written.

---

## Develop

```bash
pip install -e ".[dev]"
pytest -q          # no network or API key needed; the model is scripted
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

[Apache-2.0](LICENSE)
