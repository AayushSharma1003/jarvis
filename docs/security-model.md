# Security Model

> Status: design approved, implementation pending (phase 4). This document is normative — code that disagrees with it is wrong.

JARVIS runs shell commands, reads files, and fetches web pages, driven by an LLM that can be manipulated by anything it reads. We treat that as the threat model, not an edge case. We also say plainly what this is: **policy enforcement in a trusted process**, not OS-level sandboxing (no seccomp / sandbox-exec in v1).

## 1. Tool permission model

Every tool has a risk level: `safe` / `ask` / `dangerous`.

- `safe` (e.g. `list_dir`, `send_notification`): runs freely.
- `ask` (e.g. `write_file`, `get_clipboard` — yes, clipboard is `ask`: clipboards contain passwords): native OS dialog showing the exact action and arguments.
- `dangerous` (e.g. `delete`, anything network-writing): per-call confirmation, globally disableable.

`run_command` **always confirms, full command text shown, no exceptions.** There is no command classifier and no denylist — both are bypass generators. A future opt-in allowlist may skip confirmation for *exact-match* previously-approved commands only.

## 2. Filesystem sandbox

Filesystem tools operate only under user-configured roots. Enforcement happens on **`Path.resolve()`-ed (symlink-resolved) absolute paths** — checking the path the user typed is not enforcement. Escaping requires explicit per-path user opt-in. The extensions directory and JARVIS's own config/data directories are **permanently outside** all sandbox roots, so no tool can self-escalate by writing an extension or editing permissions.

## 3. Taint tracking (prompt-injection defense)

Delimiters around untrusted content are labeling, not defense. The mechanism:

- Content from `web_fetch`, `web_search` results, or files outside a trusted set marks the conversation **tainted**.
- From that point, *every* side-effectful tool call — regardless of its normal risk level — escalates to explicit confirmation, and the dialog says why ("this request follows content from example.com").
- Enforced in the tool-execution layer (`backend/jarvis_backend/security/taint.py`), never in the prompt.

## 4. Network guards

- `web_fetch` blocks private/link-local ranges by default (127.0.0.0/8, 10/8, 172.16/12, 192.168/16, 169.254/16, ::1, fc00::/7) — resolved-IP checked, not hostname-checked.
- Backend binds `127.0.0.1` only. WebSocket requires a per-session token *and* a strict `Origin` check (defeats browser drive-bys against localhost).

## 5. Extensions

- Manifest declares platforms, required OS permissions, and per-tool risk levels. The approval dialog at install/load shows **declared permissions**, not just source code (nobody reads 500 lines of source; everybody reads "wants: calendar access, network").
- `jarvis install <url>` pins the commit SHA at install. **No extension auto-update.**
- Extension-declared risk levels are floors, not ceilings — the core permission engine can raise them, never lower.

## 6. Credentials, screen, telemetry

- API keys never appear in prompts, logs, or exports. (Enforced by redaction at the config boundary.)
- `take_screenshot` with a **cloud backend** active warns explicitly: your screen is about to leave this machine.
- Telemetry: none. The model catalog is bundled data; refreshing it is a manual, explicit action — no automatic phone-home, ever.

## Known limitations (v1, documented on purpose)

- Same-process policy enforcement: a bug in the backend is a bug in the sandbox.
- TOCTOU windows between path resolution and file operation.
- Confirmation fatigue is a real attack surface; UX must keep confirmations rare enough to be read.
