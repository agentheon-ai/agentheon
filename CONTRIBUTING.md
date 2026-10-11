# Contributing

Thanks for helping. The project's main goal is to stay small and readable, so please keep that in mind.

## Ground rules

- **Domain stuff goes in skills and tool packs, not the platform.** If a change only helps one
  domain, it probably belongs in `examples/` or your own repo.
- **No new required services.** Agentheon must keep running with `pip install` and SQLite. New
  infrastructure belongs behind an optional extra.
- **Every behaviour change has a test.** Tests use the scripted model in `tests/conftest.py`; they
  must not need network access or an API key.
- **Never commit secrets, real customer data, or code you don't have the right to contribute.**

## Workflow

```bash
pip install -e ".[dev,mcp,search]"
pytest -q
ruff check .
```

1. Open an issue first for anything bigger than a bug fix.
2. Keep pull requests focused; explain the "why" in the description.
3. Update the README when you change configuration, endpoints or the result format.
4. Add an entry under `## [Unreleased]` in `CHANGELOG.md`.

## Releasing

Maintainers only. Bump `version` in `pyproject.toml`, move the `## [Unreleased]` entries under a
new version heading in `CHANGELOG.md`, then tag:

```bash
git tag v0.1.1 && git push origin v0.1.1
```

The release workflow checks the tag matches `pyproject.toml`, runs the tests, and publishes to PyPI
through trusted publishing — no API token is stored in the repository.

## Contributing a skill or tool pack

Add it under `examples/` with a short note at the top of the file explaining what it shows.
Tools must raise `ToolError` for expected failures and return `sources` whenever they read data.

By contributing, you agree your contributions are licensed under the Apache License 2.0.
