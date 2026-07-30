# Next-session prompt — M6.3 bug hunt

> Paste the block below into a fresh session. It is deliberately a *diagnostic*
> brief, not a task list: the point is to find what four release candidates and a
> whole verification milestone missed, not to trust that the green baseline means
> anything. Delete this file once M6.3 lands.

---

You are the technical lead on JARVIS (Tauri 2 + React 19 + Python FastAPI sidecar
+ Ollama). I'm the product owner.

Read first, in order: `docs/HANDOFF.md` — especially **"OPEN AT END OF M6.2"** at
the top of the next-action section and **gotchas 30–35**, which are all packaging
and permission traps found in the last two sessions — then
`docs/security-model.md` (normative), `docs/architecture.md`, and your
auto-loaded memory files.

**State.** Phases 1–5 complete. M6.0/M6.1/M6.2 done. The baseline is green: 722
backend tests, ruff, tsc+vite, 2 Rust. **Nothing from M6.2 is committed** —
`origin/main` is at `f8ed708` and both fixes plus a `release.yml` signature gate
are in the working tree. Commit that first (I run all git myself; emit a
📦 Milestone Commit block).

**The last two bugs were found by me clicking things, not by the suite**, and
that is the brief:

- **Gotcha 33** — the assistant said its own wake word and interrupted itself.
  Needed the model to say "Jarvis"; no test had ever asked it to introduce itself.
- **Gotcha 34** — every macOS build ever shipped had a *broken* signature, not an
  absent one. Needed a real `com.apple.quarantine` flag; CI downloads never carry
  one, so Gatekeeper was never consulted.
- **Gotcha 35** — fixing 34 changed the bundle identity, which silently revoked
  the microphone TCC grant. Voice died with a green baseline.

Each was invisible to the tests **by construction**, and each was reachable by an
ordinary user action. Assume more of them exist.

## Task 1 — close the voice regression (gotcha 35, first, it blocks everything)

Voice is dead on the installed app: neither "Hey Jarvis" nor ⌘M. It is
environmental, not code — the fix was verified acoustically before install.
Diagnose in this order and **verify each step rather than assuming**:

1. `codesign -dv /Applications/Jarvis.app | grep Identifier` → must be
   `app.jarvis-assistant.desktop`.
2. Is it translocated? `pgrep -fl "Jarvis.app/Contents/MacOS"` — an
   `AppTranslocation` path means quarantine is still set and the app is running
   read-only from a random directory.
3. Microphone grant. `TCC.db` is unreadable without Full Disk Access, so use
   `system.readiness` (it has a microphone check) or ask me to look at System
   Settings → Privacy & Security → Microphone.

Recovery is: **Open Anyway** (clears quarantine, ends translocation) → relaunch
from `/Applications` → grant the mic when prompted. Then re-run both acoustic
checks: "introduce yourself" must speak its **whole** reply, and "Hey Jarvis"
over an ordinary reply must still cut it off mid-word (`reason="stopped"`).
**Assert the control first** — enable wake via the `wake.set` message and prove
the detector fires on an idle app before trusting any negative result. A clean
pass from a disabled detector is worthless and nearly shipped once already.

## Task 2 — hunt the class of bug the suite cannot see

Do not re-run the suite and call it verification; it is green and was green
through all three gotchas above. Look specifically for **properties nobody can
observe by looking**, and prefer adding a *gate* over adding a test:

- **Packaging and permissions.** Signature, entitlements, TCC, path length,
  translocation, sandbox-relative resource lookups. `release.yml` now gates the
  macOS signature and the sidecar's bundled data; what else is asserted nowhere?
  Windows and Linux have had **no** equivalent check.
- **First-run and upgrade paths.** Every acoustic test in this project's history
  used a reply that happened not to contain "Jarvis" (gotcha 33). What else does
  every test happen to avoid? Specifically: a *second* run, an *upgrade* over an
  existing install, a machine where a permission was already denied.
- **Things a user says that a test never would.** "Introduce yourself" broke it.
  Try the ten most obvious first utterances and the ten most obvious first typed
  messages, on the packaged app, with wake on.
- **The voice path under the new signature.** Kokoro, espeak (the 151-char
  cliff), whisper and onnxruntime all load lazily and resolve paths at runtime;
  signing and translocation both move those paths.

## Task 3 — then finish v1

`v0.1.0-rc5` (rc1–rc4 are all unusable per gotcha 34) — and **put the microphone
re-grant in the release notes**, because changing signing identity revokes every
OS permission the app held. Then the two unclicked tray items, the Gatekeeper
screenshots, and A5 (not v1-blocking).

## How I want you to work

Prime directive, do no harm. **No behaviour change without a test that proves
it** — write it, watch it fail, then fix. **Mutation-prove every regression
test** (break the code, confirm the named test fails, revert; match the mutation
exactly once — gotcha 16; purge `__pycache__` between mutations — gotcha 22; a
"NOT CAUGHT" result is the useful one and usually means the *code* is
decoration). Small batches, full baseline after each. Keep text and voice
working throughout. i18n is a hard rule: backend emits machine-readable CODES
only, the frontend owns all wording (`app/src/i18n/en.json`).

Destructive or live testing uses scratch dirs only (`JARVIS_DATA_DIR` +
`JARVIS_CONFIG_DIR` at a temp path, `[filesystem] roots` at a scratch
workspace) — never my real `~/Library/Application Support/jarvis/`. Symlink
`<scratch>/data/models` to the real models dir so you don't re-download 500 MB.
Launch the packaged binary directly to pass env (`open` will not).
**Install to /Applications with a Finder drag, not `cp`** — `cp` leaves
quarantine "unmoved" and the app translocates (gotcha 35b).

Budget real time: qwen3:4b tool turns ran 3–5 minutes under memory pressure. To
tell a slow turn from a stuck one, run `lsof -nP -a -p <sidecar-pid> -iTCP` — an
ESTABLISHED connection to `:11434` means a request is genuinely in flight.

I run all git myself — never run git commands. Emit a 📦 Milestone Commit block
at the end (two `-m` flags, single-line strings, **no backticks in the message**,
they break the paste).

Push back with honest technical judgment; cutting scope is valid. Tell me which
thread you're taking first and why.
