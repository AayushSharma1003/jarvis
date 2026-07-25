# extensions/ — default extension set

Extensions shipped with the app. They go through the **same** manifest/approval/permission machinery as third-party ones — being bundled buys zero privilege. That's the point: these prove the API is sufficient.

| Extension | Why it exists |
|---|---|
| `timers-reminders/` | ✅ **Built (M5.4).** The cross-platform reference extension. Timers + reminders via local notifications. If the extension API can't express this cleanly, the API is wrong. |
| `calendar-macos/` | ⏳ Manifest only. The platform-gated reference: EventKit via pyobjc, `platforms = ["darwin"]`, declares an OS permission. Proves platform gating + TCC declaration work. Lives here so an EventKit breakage is an extension patch, not a core release. |

**What `timers-reminders` proved.** It held up its half of the bargain above: the
API *couldn't* express it. A tool takes a call and returns a string, and a timer's
whole job happens later, with nothing waiting on a return value. That gap is what
[`extensions/host.py`](../backend/jarvis_backend/extensions/host.py) exists to
close — `notify()` for saying something unprompted, `state_dir()` for saving state
somewhere that isn't inside your own digest. Read the extension before writing one;
its docstrings are where the traps are written down.

**These are not installed for you.** Being bundled buys zero privilege, and it also
buys zero convenience right now: copy the folder into your extensions directory and
approve it like any other. See the authoring guide.

Authoring guide: [docs/extensions.md](../docs/extensions.md).
