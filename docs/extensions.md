# Extension Authoring Guide

> Status: the manifest, the content-keyed approval, the loader (M5.1), the in-app
> approval panel (M5.2), `jarvis install` (M5.3) and the host API (M5.4) are all built,
> and [`timers-reminders`](../extensions/timers-reminders/) is a working reference you
> can read. `calendar-macos` is still a manifest with no code.

An extension is a folder containing `manifest.toml` + `extension.py`, living in the user's extensions directory (`<data dir>/extensions`, which is **permanently outside** the filesystem sandbox — see [security-model.md](security-model.md) §2 and §5).

## What approving an extension means

Read this before writing one, because it shapes what your manifest is for.

**An approved extension runs with the same access Jarvis has.** It is imported into the
Python sidecar; `network = false` in your manifest does not stop `import socket`, and
`os = []` does not stop it reading `~/.ssh`. The `[permissions]` block is a
**declaration of intent** shown to the user at approval — it is how they judge whether
to run your code, not a cage around it. Enforcing it for real would need one subprocess
per extension behind an RPC boundary; v1 does not do that and does not pretend to.

What *is* enforced: approval covers the **exact bytes** of your folder (SHA-256 over
every file, `manifest.toml` included). Change anything and the extension goes back to
pending until the user approves again. Only functions you declare in `[[tools]]` are
exposed to the model, your declared risk levels are **floors** the core can raise, and a
tool name already taken by the core or another extension is refused.

## Install paths

1. Drop the folder in the extensions directory. It is **detected**, not trusted — it
   shows up as `pending` and does not load until approved.

   The **bundled defaults arrive on their own** (M6.0): the sidecar copies them into
   the extensions directory on startup if they are not already there. Seeding
   delivers bytes and does not bless them — a seeded extension is `pending` like any
   other, and shipping a default is not consent to run it. Nothing is ever
   overwritten, because an extension that updates itself is an extension whose
   approved bytes are a suggestion (§5): a newer bundled version simply hashes
   differently and reads as `changed` until a human looks at it again.

   Which extensions ship is an explicit list (`BUNDLED` in
   `backend/jarvis_backend/extensions/bundled.py`), not "whatever is in the folder"
   — `calendar-macos` is a manifest with an empty `extension.py`, and seeding it
   would put an extension in every user's panel that fails the moment it is
   approved.
2. `jarvis install <url>` clones a git repository, pins the commit it fetched, shows
   the declaration, and asks — the same prompt `jarvis extensions approve` uses,
   because there is exactly one way an extension becomes runnable.

   ```sh
   jarvis install https://github.com/someone/an-extension
   jarvis install https://github.com/someone/an-extension --ref v1.2.0
   jarvis install <url> --force        # replace one that's already installed
   ```

   Only `http://` and `https://` are accepted. That is a real check, not
   tidiness: `git clone 'ext::sh -c "…"'` **executes the command**, so a pasted
   URL with any other transport is refused before git is invoked.

   The folder is named after your **manifest**, not the repository, and the
   destination is created only after the clone has been validated in a staging
   directory — a repo cannot choose the name it installs as. Answering "no" to
   the prompt leaves it installed and `pending`, so you can approve it later
   without downloading it again. **No auto-update, ever**: a `--force`
   reinstall changes the bytes, which changes the digest, so it lands as
   `changed` and does not run until you approve it again.

   One wrinkle worth knowing: the CLI is a **different process** from the
   running sidecar, so an extension installed while the app is open shows as
   approved-but-not-running until you restart Jarvis — or press Approve in the
   panel, which loads it live. The CLI says so when it finishes.

Either way, approve it explicitly — from the app's **Extensions panel** (the puzzle icon
in the header; a badge flags anything awaiting review) or the CLI. Revoking is not the
same as unloading: it removes your tools, but a thread you started keeps running until
Jarvis restarts, so a revoked `timers-reminders` still fires the timers it had pending.
The panel says as much; closing that gap properly needs the per-extension subprocess
that §5 says would also be needed to enforce `[permissions]`.

```sh
jarvis extensions list                 # what's installed, and its status
jarvis extensions approve <name>       # prints what it declares, then asks
jarvis extensions revoke <name>
```

Both show you the declared permissions and the effective risk of each tool before asking,
and both key the approval to the extension's exact bytes. Approving in the panel loads the
extension immediately — no restart. Revoking removes its tools immediately too, but code
it already ran at import stays in memory until you restart; the panel says so.

Statuses you may see: `pending` (never approved), `approved`, `changed` (approved once,
edited since — the version you approved keeps running until you re-approve or revoke),
`unsupported_platform`, `invalid` (with a code saying why).

## Manifest

```toml
[extension]
name = "my-extension"        # must match the folder name; [a-z0-9][a-z0-9-]*
version = "0.1.0"
description = "One line, shown in the approval dialog"
platforms = ["darwin", "win32", "linux"]  # omit = all; matched against sys.platform

[permissions]
os = []            # e.g. ["calendar"] — surfaced at approval; macOS OS-permissions
                   # additionally require a usage string pre-declared in the app
                   # bundle (see "the Info.plist caveat" below)
network = false    # advisory, with one enforced consequence: true raises every
                   # tool of this extension to at least `ask`

[[tools]]
name = "my_tool"   # [a-z_][a-z0-9_]* — this is the name the model calls
risk = "ask"       # floor, not ceiling — the core engine can raise, never lower
```

Every field above is validated, and anything unrecognised is **refused rather than
defaulted** — an invented `risk = "trusted"` does not quietly become `safe`. Unknown
*keys* are ignored, so a manifest written for a later version still loads.

## extension.py

Exports plain functions with type-hinted signatures; the loader introspects them into
tool schemas exactly as it does for core tools, and the **first line of each docstring
becomes the description the model sees** — write it for the model, not for you.

```python
def set_timer(minutes: int, label: str = "") -> str:
    """Set a timer that notifies the user when it finishes."""
    ...
```

**One file.** `sys.path` is deliberately not modified — an extension shipping `json.py`
would shadow the stdlib for the whole process — so v1 has no multi-file extensions and
no third-party imports beyond what the sidecar already ships.

**No symlinks anywhere in the folder.** A symlinked file would be imported while its
real bytes live outside the folder, free to change after approval, so the whole
extension is refused (`EXTENSION_UNSAFE_TREE`).

**Your description is the docstring's first paragraph, and there are no per-argument
descriptions.** `Registry.register` takes `inspect.getdoc(fn).split("\n\n")[0]`, and
the loader has no `params` to pass, so anything the model needs to know about an
argument's *format* has to be in that first paragraph — a blank line before it and
the model is guessing. This is why `set_reminder`'s docstring reads the way it does.

## The host API (M5.4)

Everything above describes a tool: the model calls it, it returns a string. That is
the whole contract, and it cannot express anything that happens **later** — a timer
firing has no request in flight and nothing waiting on a return value. One module
exists for that, and it is the only thing an extension is invited to import from the
core:

```python
from jarvis_backend.extensions.host import notify, state_dir
```

### `notify(source, code, data=None, *, speak=False)`

Tells the user something happened. Safe to call from **any thread** (a scheduler
thread is the normal caller) and safe to call when nothing is listening.

`code` is machine-readable and the **frontend owns the wording** — the same i18n rule
the rest of the backend follows. `data` carries values to interpolate, and it is
content the user supplied (a timer's label), never English you wrote. Add your codes
to `app/src/i18n/en.json` under `notification.code.*`; one the UI has never heard of
renders a neutral "<your extension> sent a notification" rather than a raw code.

Pick the *message* in your extension rather than passing a maybe-empty field and
letting the UI branch — `timers-reminders` emits `TIMER_FINISHED` or
`TIMER_FINISHED_LABELED`, not one code with an optional label.

`speak=True` asks Jarvis to say it out loud. That matters more than the toast: the
backend owns the speaker, so a spoken notification reaches someone who minimised the
window, and the toast does not.

Bounds you should know about, none of them negotiable from your side:

- **Rate limited globally** (10/minute). Rotating `source` does not help — the limit
  is deliberately not per-source, because `source` is a string you chose and nothing
  verifies it.
- **`data` is sanitized**: non-JSON values become `repr`, long values are truncated,
  and only the first dozen keys survive. A value `send_json` cannot encode would take
  the WebSocket down, and that is not a failure your extension should be able to cause.
- **It returns `""` when the notification was dropped.** There is nothing useful to do
  about that; it is returned so tests can see it.

### `state_dir(name)`

A private, writable directory for your extension, created on demand.

**Never write inside your own extension folder.** Your approval is keyed on a SHA-256
of every file in it, so saving a file there changes the digest, flips you to `changed`,
and stops you loading on the next start — an extension that un-approves itself the
first time it saves anything. This directory is a sibling of `extensions/`, outside
every hashed tree.

### Resuming after a restart

If you keep state that has to *act* later, remember that nothing will call you to wake
it up. Your module body is the only code that runs on load, so a scheduler must be
started from there when there is work pending — otherwise a reminder set yesterday
sits in your state file forever and the only way to trigger it is for the user to
happen to set a new one. See the bottom of `timers-reminders/extension.py`; that
exact bug shipped for about an hour during M5.4 and was caught by restarting the app,
not by any test.

Prefer absolute deadlines (`time.time()`) and a poll over `threading.Timer`:
`time.monotonic()` does not advance while macOS is asleep, so a countdown armed
before a closed lid fires late by however long the machine slept.

### What the host API is not

It is a **convenience, not a boundary**. Your extension already runs in the sidecar
process with everything it can do — it could reach the connection list by importing
the server module, or open its own socket. A stable front door means you are not
coupled to internals that move; it does not narrow what you are able to do, and
nothing here should be read as a sandbox.

Worked examples: [`extensions/timers-reminders/`](../extensions/timers-reminders/) (cross-platform reference) and [`extensions/calendar-macos/`](../extensions/calendar-macos/) (platform-gated + OS-permission reference).

## A note on `risk = "safe"`

`safe` means *this tool changes nothing*. The core cannot verify that claim about
third-party code, so an extension's `safe` tool is treated as read-only for everyday
purposes — it runs without a prompt — but it does **not** get the exemption that lets
core read-only tools skip taint escalation. Once untrusted content is in a conversation
(§3), your `safe` tool confirms like anything else. Declare `safe` only for tools that
genuinely read; declare `ask` if yours writes something the user would want to see
coming.

## The Info.plist caveat

Extension code isolation does not extend to macOS TCC permissions: an OS permission (calendar, contacts, …) needs its usage string in the **core app's** Info.plist. The core bundle pre-declares strings for the default extension set; third-party extensions needing novel TCC permissions won't work until the string ships in a core release. This is an OS constraint, not a design choice.

## Other extension points

- **TTS voices**: implement `backend/jarvis_backend/tts/base.py`
- **LLM backends**: implement `backend/jarvis_backend/llm/base.py` (OpenAI-compatible endpoints need no code at all — just settings)

Neither goes through the manifest/approval machinery above, which covers tools only.
