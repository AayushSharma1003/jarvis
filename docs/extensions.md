# Extension Authoring Guide

> Status: API design pending (phase 4). This documents the intended shape.

An extension is a folder containing `manifest.toml` + `extension.py`, living in the user's extensions directory (which is **permanently outside** the filesystem sandbox — see [security-model.md](security-model.md) §2 and §5).

## Install paths

1. Drop the folder in the extensions directory → approved on next load.
2. `jarvis install <github-url>` → clones, pins the commit SHA, shows **declared permissions** for approval, installs. No auto-update.

## Manifest

```toml
[extension]
name = "my-extension"
version = "0.1.0"
description = "One line, shown in the approval dialog"
platforms = ["darwin", "win32", "linux"]  # omit = all

[permissions]
os = []            # e.g. ["calendar"] — surfaced at approval; macOS OS-permissions
                   # additionally require a usage string pre-declared in the app
                   # bundle (see "the Info.plist caveat" below)
network = false    # true => every network call is taint-relevant

[[tools]]
name = "my_tool"
risk = "ask"       # floor, not ceiling — the core engine can raise, never lower
```

## extension.py

Exports plain functions with type-hinted signatures; the loader introspects them into tool schemas. Full worked examples: [`extensions/timers-reminders/`](../extensions/timers-reminders/) (cross-platform reference) and [`extensions/calendar-macos/`](../extensions/calendar-macos/) (platform-gated + OS-permission reference).

## The Info.plist caveat

Extension code isolation does not extend to macOS TCC permissions: an OS permission (calendar, contacts, …) needs its usage string in the **core app's** Info.plist. The core bundle pre-declares strings for the default extension set; third-party extensions needing novel TCC permissions won't work until the string ships in a core release. This is an OS constraint, not a design choice.

## Other extension points

- **TTS voices**: implement `backend/jarvis_backend/tts/base.py`
- **LLM backends**: implement `backend/jarvis_backend/llm/base.py` (OpenAI-compatible endpoints need no code at all — just settings)
