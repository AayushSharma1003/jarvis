# extensions/ — default extension set

Extensions shipped with the app. They go through the **same** manifest/approval/permission machinery as third-party ones — being bundled buys zero privilege. That's the point: these prove the API is sufficient.

| Extension | Why it exists |
|---|---|
| `timers-reminders/` | The cross-platform reference extension. Timers + reminders via local notifications. If the extension API can't express this cleanly, the API is wrong. |
| `calendar-macos/` | The platform-gated reference: EventKit via pyobjc, `platforms = ["darwin"]`, declares an OS permission. Proves platform gating + TCC declaration work. Lives here so an EventKit breakage is an extension patch, not a core release. |

Authoring guide: [docs/extensions.md](../docs/extensions.md).
