# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `py.typed` marker, so type checkers use Agentheon's annotations downstream.
- `NOTICE` file and the Apache 2.0 appendix with the copyright line filled in.
- Project URLs, maintainer and trove classifiers in `pyproject.toml`.
- CI now installs the `search` extra, builds the distribution and asserts the wheel actually
  contains the builtin skills, the web page and `py.typed`; the Docker job boots the image and
  checks `/api/v1/ready` reports `status: ok`.
- Release workflow publishing to PyPI on a tag via trusted publishing.
- Issue and pull request templates, Dependabot for pip and GitHub Actions.

### Changed

- The web page was rebuilt. Each question now renders as a run record: a numbered live step log
  of the real Server-Sent Events, then the answer, provenance chips, cited sources, any sources
  the grounding gate dropped, and the raw gates and trace. Gate state is carried by colour and
  shape, not just text, so a regeneration or a fail-closed reads at a glance. A header strip
  reports the model, skill, tool and rule counts, and flags when zero rules are loaded — the case
  where nothing is being enforced. Answers render markdown (headings, lists, tables, code, links)
  through a small escape-first renderer, so a model cannot inject HTML. Creating a watch is a real
  form rather than a chain of `prompt()` calls. Still one file, still no build step, no CDN and no
  dependencies, and still light and dark.
- `AUTH_DEV_MODE` now defaults to `false` in `.env.example`. Copying the example and deploying it
  no longer grants keyless admin access.
- Docker image base moved to `python:3.13-slim`, matching the newest version CI tests.

### Fixed

- Every model call sent `temperature=0.2`, which recent reasoning models — Claude Sonnet 5,
  Claude Opus 5 and the Claude Opus 4.7+ family — reject with a 400. LiteLLM's `drop_params`
  could not help, because temperature is a valid parameter for the provider in general, so every
  request against those models failed. Temperature is now only sent when `LLM_TEMPERATURE` is set.
- No `max_tokens` was ever sent. On models that think before answering it caps the thinking and
  the reply together, so a small provider default could spend the budget on thinking and truncate
  the reply — and a truncated reply is not valid JSON, which is the protocol the engine relies on.
  It is now always sent and configurable via `LLM_MAX_TOKENS` (default 8192).
- Governance no longer fails open. A `GOVERNANCE_FILE` that exists but is not a regular file —
  what Docker creates when a bind mount points at a missing file — raised no error and left the
  platform running with zero rules while `/api/v1/ready` still reported `ok`. It now fails closed
  with an explanatory error.
- Rule patterns containing control characters are rejected at load time. In double-quoted YAML,
  `"\b"` is a backspace rather than a regex word boundary, so a `forbid` rule written that way
  compiled cleanly and then silently never matched.
- `skills/` is present in the repository, so `docker compose up` no longer has Docker create it.

## [0.1.0] - 2026-10-11

Initial release: the governed, skills-based agent harness — planner, per-skill tool loops,
compliance and grounding gates, expiring learned knowledge, watches, audit trail, web UI, REST API
and MCP server.

[Unreleased]: https://github.com/agentheon-ai/agentheon/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/agentheon-ai/agentheon/releases/tag/v0.1.0
