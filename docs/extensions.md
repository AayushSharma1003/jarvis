# Extension Authoring Guide

> Status: the manifest, the content-keyed approval, the loader (M5.1) and the in-app
> approval panel (M5.2) are built. `jarvis install` is M5.3; until then, install by
> hand (below). The default extensions in [`extensions/`](../extensions/) are M5.4 —
> their manifests are written, their code is not.

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
2. `jarvis install <github-url>` (M5.3) → clones, pins the commit SHA, shows declared
   permissions for approval, installs. No auto-update.

Either way, approve it explicitly — from the app's **Extensions panel** (the puzzle icon
in the header; a badge flags anything awaiting review) or the CLI:

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
