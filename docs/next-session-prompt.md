# Next-session prompt — security + product audit

Paste the block below into a fresh session. Delete this file once the audit lands.

---

You are the technical lead on JARVIS (Tauri 2 + React 19 + Python FastAPI sidecar
+ Ollama). I'm the product owner. **v0.1.0-rc6 is published and I am about to hand
the download link to friends on macOS, Windows and Linux.** Before I do, I want an
audit. This session is not about building features — it is about finding what is
wrong, unsafe, or embarrassing while it is still cheap.

**Read first, in order:** `docs/HANDOFF.md` — the whole gotcha list, but 30–40
especially, and "OPEN AT END OF M6.3" — then `docs/security-model.md` (normative),
`docs/architecture.md`, `README.md`, and your auto-loaded memory files.

## State

Phases 1–5 complete. M6.0–M6.3 done. Baseline green: **733 backend tests, ruff,
tsc + vite, 2 Rust**. `main` is pushed; `v0.1.0-rc6` is published as a pre-release
with four installers and `SHA256SUMS.txt`. The README is a product page with per-OS
download buttons.

**Do not treat the green baseline as evidence of anything.** In the last two
sessions it was green through *seven* separate user-facing bugs: a broken macOS
signature on every build ever shipped, a hardened runtime that silently denied the
microphone, a mic open that froze the whole backend, a readiness gate that called a
deaf machine ready, an assistant that interrupted itself by saying its own name, a
release gate that could never have run, and voice models that no downloader could
obtain. Every one was invisible to CI and reachable by an ordinary user action.

**The pattern, which is your best search heuristic:** the development machine has
something the shipped product does not (a repo, a CLI, a granted permission, a
model file, an audio device), and every check runs on the development machine.

## Task 1 — Security audit

`docs/security-model.md` is normative; where code and doc disagree, say so
explicitly rather than assuming the doc is aspirational. Cover at least:

- **The new download path (`assets.fetch`, `assets.py`) — highest priority, it is
  the newest code and the least reviewed.** It takes a URL, writes a file, and
  verifies a pinned sha256. Check: can any input steer the destination path
  (filename traversal)? Is the checksum bypassable on resume, where a `.part` is
  appended to? What happens on a partial/hostile response, a redirect to a
  different host, a wrong `Content-Length`? Is there a disk-space or size ceiling?
  Can it be triggered repeatedly to fill the disk, or by an extension rather than a
  user?
- **The WS token.** It is passed to the sidecar in its environment, and I confirmed
  this session that `ps eww <pid>` prints it — any process running as the same user
  can read it and then drive a backend that has file and shell tools. The security
  model says "127.0.0.1 + token + Origin check". Is same-user local process access
  in the threat model? If it is accepted, it should be written down; if not, it
  needs a fix.
- **Permission engine, sandbox, taint, SSRF** — re-derive rather than trusting the
  tests: symlink resolution order, `..` traversal, the confirm broker's every
  not-an-answer path resolving to deny, session grants keyed on exact arguments,
  taint surviving a conversation branch, redirect re-validation and IP-literal
  handling in `web_fetch`.
- **Extensions.** An approved extension runs unsandboxed with the sidecar's
  privileges — that is the documented design, so audit the *approval gate*: is the
  content hash actually what is loaded (TOCTOU between approval and import)? Does
  `jarvis install` still refuse non-`http(s)` URLs and derive the folder name from
  the manifest?
- **Release integrity.** `SHA256SUMS.txt` is served from the same GitHub release as
  the binaries, so it is not an independent integrity source. Decide whether that is
  worth stating in `unsigned-install.md`.

## Task 2 — Product / first-run audit

The question is whether an ordinary person, on a machine that has never seen this
app, gets to a working assistant without help. **Drive the packaged app, not the
repo.** `backend/tests/manual/probe_voice_live.py` (`control` / `mic` / `acoustic`)
drives the real running app over its WebSocket and auto-discovers port and token —
run `control` FIRST, always; it is what stops a pass from a switched-off detector.

Exercise the paths nothing has:

- **Failure states during the 500 MB model download**: no network, network dropping
  mid-download, quitting the app mid-download (does resume work, or is the `.part`
  orphaned?), a full disk. There is currently **no cancel button** — decide whether
  that is acceptable.
- **First run with no Ollama at all**, which is the single most likely state of a
  friend's machine.
- **Second run, and upgrade over an existing install.**
- **The wake toggle switched on before the wake models exist.**
- **The ten most obvious first utterances and first typed messages**, on the
  packaged app. "Introduce yourself" is what found gotcha 33.

## Concrete leads already found — start here, they are free

1. **25 backend CODES have no `en.json` key.** Most are env-var names and internal
   codes, but these look genuinely user-facing: `CLONE_FAILED`, `CLONE_TIMEOUT`,
   `GIT_NOT_FOUND`, `GIT_TERMINAL_PROMPT`, `EXTENSION_ALREADY_INSTALLED`,
   `EXTENSION_TOO_LARGE`, `CONFIG_PARSE_ERROR`, `CONFIG_INVALID_VALUE`,
   `TURN_NOT_FOUND`, `EMPTY_TURN`, `PARENT_TURN_MISMATCH`, `TREE_CYCLE`,
   `VAD_BAD_CHUNK`. Work out which can actually reach the UI; each of those shows a
   user a raw SCREAMING_CODE, which breaks the project's i18n rule. **Consider a
   test that fails when a reachable code has no key** — a gate beats a one-time fix.
2. **`_ASSET_OPENER` in `server/app.py`** is a module-global test seam mutated by
   the suite. It is `None` in production, but confirm no path can set it at runtime.
3. **README download links are pinned to `v0.1.0-rc6`** and will 404 after the next
   tag. GitHub's `/releases/latest/` skips pre-releases, which is why they are
   explicit. Either bump them per release or make the next one a full release.
4. **The espeak first-run copy under App Translocation** has still never been
   exercised (gotcha 31 fallback, gotcha 35b).

## How I want you to work

**Prime directive: do no harm.** No behaviour change without a test that proves
it — write it, watch it fail, then fix. **Mutation-prove every regression test**:
break the code, confirm the *named* test fails, revert; match the mutation exactly
once (gotcha 16); purge `__pycache__` between mutations (gotcha 22). A "NOT CAUGHT"
result is the useful one — three of them in the last session were the only reason
the tests were worth anything.

**Verify the artifact, not a proxy.** Run gates against the shipped dmg, not an
intermediate. Extract a workflow's gate body from the YAML and run it verbatim
rather than retyping it. Check the bytes a user downloads.

Small batches, full baseline after each. Keep text and voice working throughout.
i18n is a hard rule: backend emits machine-readable CODES only, the frontend owns
all wording (`app/src/i18n/en.json`).

Destructive or live testing uses scratch dirs only (`JARVIS_DATA_DIR` +
`JARVIS_CONFIG_DIR` at a temp path) — never my real
`~/Library/Application Support/jarvis/`. Symlink `<scratch>/models` to the real
models dir so you don't re-download 500 MB. Launch the packaged binary directly to
pass env (`open` will not).

**Report findings honestly, ranked by whether a user will actually hit them.** I
would rather hear "this is fine" with evidence than a padded list. If something is
a real risk you cannot fix cheaply, say so and tell me the cost.

**You have push access.** Commit and push fixes yourself; I no longer need commit
blocks. **Do not tag or publish a release without asking me** — that is a
publishing decision.

## Then finish v1

Once the audit is clean, or its findings are fixed and pushed: cut and publish the
next release, then the two remaining owner items — the two tray menu items have
still never been clicked, and the Gatekeeper screenshots need a browser download on
a Mac that has not already approved the app.

**Still true and worth stating: nobody has ever run the Windows or Linux builds.**
They compile and checksum; no human has opened them. If you can find a way to
smoke-test either without hardware, that is worth more than most of this list.

Push back with honest technical judgment; cutting scope is valid. Tell me which
thread you're taking first and why.
