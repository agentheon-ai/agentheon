<!-- Thanks for contributing. Please read CONTRIBUTING.md first; it is short. -->

## What and why

<!-- What changes, and what problem it solves. Link the issue it closes. -->

Closes #

## How it was verified

<!-- Commands you ran, and what you saw. -->

```
pytest -q
ruff check .
```

## Checklist

- [ ] Every behaviour change has a test, using the scripted model in `tests/conftest.py`
      (no network access and no API key needed).
- [ ] `pytest -q` and `ruff check .` pass.
- [ ] The README is updated if configuration, endpoints or the result format changed.
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`.
- [ ] This keeps the platform domain-agnostic — domain-specific work belongs in a skill or tool
      pack under `examples/`, not in the platform.
- [ ] No new required services; new infrastructure sits behind an optional extra.
- [ ] No secrets, real customer data, or code I do not have the right to contribute.
