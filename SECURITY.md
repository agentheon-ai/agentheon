# Security

## Supported versions

Agentheon is pre-1.0. Fixes land on `main` and in the next release; only the latest release is
supported.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| < 0.1 | ❌ |

## Reporting a vulnerability

Please do not open a public issue.

Report privately through
[GitHub Security Advisories](https://github.com/agentheon-ai/agentheon/security/advisories/new)
("Report a vulnerability" on the Security tab). If that is unavailable to you, email
**saisanirose@gmail.com** with `SECURITY` in the subject.

Include the version or commit, what an attacker can achieve, and steps to reproduce. You will get
an acknowledgement within 7 days and an assessment within 14. Please give us a chance to ship a fix
before disclosing publicly; we will credit you in the advisory unless you prefer otherwise.

## Running Agentheon safely

- Never set `AUTH_DEV_MODE=true` on a server reachable by others. It grants keyless admin access.
  It ships as `false` in `.env.example` — keep it that way outside your laptop.
- Generate long random API keys (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
  `API_KEYS` holds them in plaintext in the environment, so treat it like any other secret.
- Tool packs run with the server's permissions. Give database tools read-only credentials and
  validate inputs inside the tool.
- `fetch_page` blocks private, loopback, link-local and reserved addresses, but it resolves the
  hostname before connecting and cannot defend against every DNS rebinding trick. Put the server
  on a network that cannot reach anything sensitive anyway.
- Governance rules are only as good as their patterns. `agentheon check` reports how many rules
  loaded; if it says `0 rules` and you expected more, Agentheon is not enforcing anything.
- Model output is untrusted: governance rules and grounding reduce risk, they do not remove it.
  Keep a human in the loop for decisions that matter.
- Learned notes are redacted for emails, phone numbers, card and ID numbers, which is a filter and
  not a guarantee. Use `private: true` or `LEARNING_ENABLED=false` for sensitive work.
