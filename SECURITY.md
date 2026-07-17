# Security Policy

## Reporting a vulnerability

Open a private security advisory on GitHub (Security → Advisories → Report a vulnerability). Please do not open public issues for exploitable bugs. Expect an acknowledgment within a week — this is a spare-time project.

## Scope

Particularly interested in:

- Sandbox escapes in filesystem tools (symlink tricks, TOCTOU, path normalization)
- Bypasses of the taint-escalation model via crafted web content or file content
- Confirmation-dialog spoofing or bypass
- SSRF guard bypasses in `web_fetch`
- Extension permission-declaration bypasses
- Local API (WebSocket) auth or Origin-check bypasses

## The security model itself

Design doc: [docs/security-model.md](docs/security-model.md). If you think the *model* is wrong (not just an implementation bug), open a discussion — that's a design conversation we want to have in public.
