# Security

## Reporting a vulnerability

Please do not open a public issue. Report privately through GitHub's
"Report a vulnerability" (Security tab), with steps to reproduce. We aim to reply within 7 days.

## Running Agentheon safely

- Never set `AUTH_DEV_MODE=true` on a server reachable by others.
- Generate long random API keys (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
- Tool packs run with the server's permissions. Give database tools read-only credentials and
  validate inputs inside the tool.
- `fetch_page` blocks private and local addresses, but put the server on a network that cannot
  reach anything sensitive anyway.
- Model output is untrusted: governance rules and grounding reduce risk, they do not remove it.
  Keep a human in the loop for decisions that matter.
