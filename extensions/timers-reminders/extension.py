"""Timers and reminders — the cross-platform reference extension (M5.4).

This is the extension the API is measured against (see ../README.md). It uses
exactly what docs/extensions.md documents and nothing else: one file, the
standard library, declared tools, and the host API for the one thing a plain
function cannot do — say something when nobody asked.

Three decisions here are worth more than the code, because each one is a trap
that looks fine until it isn't:

1. **The scheduler polls wall-clock time; it does not sleep until the deadline.**
   `threading.Timer(3600, ...)` waits on a monotonic clock, and on macOS
   `time.monotonic()` **does not advance while the machine is asleep**. Arm an
   hour timer, shut the lid for two hours, and it fires an hour after you open
   it again. The primary target is a laptop that sleeps constantly, so every
   deadline is stored as an absolute `time.time()` and a 1 s poll fires whatever
   is overdue. The same choice is what makes persistence correct: an absolute
   timestamp survives a restart, a countdown does not.

2. **State is saved via `host.state_dir()`, never beside this file.** An
   extension's approval is keyed on a SHA-256 of every file in its folder
   (§5), so writing `timers.json` next to `extension.py` would change the
   digest, flip this extension to `changed`, and stop it loading on the next
   start — an extension that un-approves itself the first time it saves
   anything.

3. **Everything is bounded.** `MAX_PENDING` is what makes the `safe` risk level
   in the manifest defensible: the risk of an unconfirmed `set_timer` is not
   one unwanted timer, it is a model looping and producing thousands. The
   answer to a volume problem in this codebase is a cap, not a confirmation
   dialog the user learns to dismiss.

Risk levels, decided rather than inherited (see manifest.toml): all four tools
are `safe`. An extension's `safe` is not read-only (§3, gotcha 21), so these
already escalate to a confirmation once untrusted content is in the
conversation — which is the protection worth having. Putting a dialog in front
of "set a timer for ten minutes" in a *clean* conversation would be the
confirmation fatigue §1 names as an attack surface.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any

from jarvis_backend.extensions.host import notify, state_dir

NAME = "timers-reminders"

# The cap that lets these tools be `safe`. A user has a handful of timers; a
# model in a loop has thousands.
MAX_PENDING = 32

# How often the scheduler looks at the clock. A fired timer is late by at most
# this much, which nobody can perceive, and the thread is asleep the rest of the
# time — negligible beside the 2.4% the always-on wake word costs.
POLL_INTERVAL_S = 1.0

# Refuse a timer longer than a week: past that it is a reminder, and a number
# that large is far more likely to be a model's mistake than a user's intent.
MAX_TIMER_MINUTES = 7 * 24 * 60

# A deadline missed by more than this was missed because Jarvis was not running,
# not because the poll was slow. Only that case is worth telling the user about.
LATE_AFTER_S = 90.0

MAX_TEXT_CHARS = 120


class TimerError(Exception):
    """Carries a machine-readable code; the frontend owns the wording.

    `tools/registry.py` reads `.code` off any exception a tool raises, so this
    is all it takes to get a translated failure into the tool span.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _Schedule:
    """The pending timers, the file they live in, and the thread that fires them.

    One lock guards everything: tool calls arrive on `asyncio.to_thread` worker
    threads while the scheduler mutates the same list from its own.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: list[dict[str, Any]] = []
        self._next_id = 1
        self._thread: threading.Thread | None = None
        self._path = state_dir(NAME) / "timers.json"
        self._load()
        if self._pending:
            # Anything read back from disk is already overdue or soon will be,
            # and nothing else is going to wake the poll — see the resume call
            # at the bottom of this file.
            self._ensure_thread()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        """Read what survived the last shutdown. A file we cannot read is an
        empty schedule, never a crash — the same posture as ApprovalStore."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            entries = raw["pending"]
        except (OSError, ValueError, KeyError, TypeError):
            return
        if not isinstance(entries, list):
            return
        for entry in entries[:MAX_PENDING]:
            if isinstance(entry, dict) and isinstance(entry.get("due_at"), int | float):
                self._pending.append(entry)
        self._next_id = 1 + max((int(e.get("seq", 0)) for e in self._pending), default=0)

    def _save(self) -> None:
        """Atomic: a crash mid-write must not leave a half-file that reads as
        an empty schedule and silently drops every pending reminder."""
        payload = json.dumps({"pending": self._pending}, indent=1)
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            # A timer that fires but is not persisted is better than no timer.
            pass

    # -- the scheduler ------------------------------------------------------

    def _ensure_thread(self) -> None:
        """Started on the first scheduled item, not at import.

        A user who never sets a timer never pays for a thread, and — more
        practically — importing this module in a test does not leak one.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name=f"{NAME}-scheduler", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            time.sleep(POLL_INTERVAL_S)
            try:
                self.tick(time.time())
            except Exception:  # noqa: BLE001 - the scheduler must outlive one bad entry
                pass

    def tick(self, now: float) -> list[dict[str, Any]]:
        """Fire everything due at `now`. The whole scheduler, minus the sleep.

        Pure enough to drive from a test with an injected clock, which is why
        the sleeping lives in `_run` and nothing else knows about it.

        Fired entries are removed and saved **before** they are announced, so a
        crash between the two loses a notification rather than replaying it on
        every start.
        """
        with self._lock:
            due = [e for e in self._pending if e["due_at"] <= now]
            if not due:
                return []
            self._pending = [e for e in self._pending if e["due_at"] > now]
            self._save()

        for entry in due:
            _announce(entry, now)
        return due

    # -- the schedule itself ------------------------------------------------

    def add(self, kind: str, due_at: float, text: str, **extra: Any) -> dict[str, Any]:
        with self._lock:
            if len(self._pending) >= MAX_PENDING:
                raise TimerError("TIMER_LIMIT_REACHED")
            entry: dict[str, Any] = {
                "id": f"t{self._next_id}",
                "seq": self._next_id,
                "kind": kind,
                "due_at": due_at,
                "text": text[:MAX_TEXT_CHARS],
                **extra,
            }
            self._next_id += 1
            self._pending.append(entry)
            self._save()
        self._ensure_thread()
        return entry

    def cancel(self, timer_id: str) -> dict[str, Any]:
        with self._lock:
            for i, entry in enumerate(self._pending):
                if entry["id"] == timer_id:
                    self._pending.pop(i)
                    self._save()
                    return entry
        raise TimerError("TIMER_NOT_FOUND")

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self._pending, key=lambda e: e["due_at"])


def _announce(entry: dict[str, Any], now: float) -> None:
    """Turn one fired entry into a notification. Codes only — never a sentence.

    The extension picks between the labelled and unlabelled variants here
    rather than passing a maybe-empty `label` and letting the UI branch on it:
    choosing the message is the extension's job, rendering it is the UI's.
    """
    late = now - entry["due_at"] > LATE_AFTER_S
    if entry["kind"] == "reminder":
        code = "REMINDER_DUE_LATE" if late else "REMINDER_DUE"
        data: dict[str, Any] = {"text": entry["text"]}
        if late:
            data["due"] = _format_local(entry["due_at"])
    elif entry["text"]:
        code, data = "TIMER_FINISHED_LABELED", {"label": entry["text"]}
    else:
        code, data = "TIMER_FINISHED", {"minutes": entry.get("minutes", 0)}
    notify(NAME, code, data, speak=True)


def _format_local(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def _parse_when(when: str) -> float:
    """An ISO-8601 local datetime → an absolute timestamp.

    Natural language ("next Tuesday", "in an hour") is deliberately NOT parsed
    here. The model reading this tool's description is a far better date parser
    than anything that would fit in this file, and it already knows what day it
    is from `get_datetime`. Refusing anything else keeps the failure loud and
    the extension small.
    """
    if not isinstance(when, str) or not when.strip():
        raise TimerError("REMINDER_TIME_INVALID")
    try:
        moment = datetime.fromisoformat(when.strip())
    except ValueError as e:
        raise TimerError("REMINDER_TIME_INVALID") from e
    return moment.timestamp()


_SCHEDULE: _Schedule | None = None


def _schedule() -> _Schedule:
    """Built on first use, not at import.

    Importing *is* executing (§5), and a module body that reads and writes the
    filesystem runs the instant a user clicks Approve. Deferring it keeps the
    import itself boring, which is what the approval was for.
    """
    global _SCHEDULE
    if _SCHEDULE is None:
        _SCHEDULE = _Schedule()
    return _SCHEDULE


# -- the declared tools -----------------------------------------------------
#
# Only these four are in manifest.toml's [[tools]], so only these four are ever
# registered — everything above is an ordinary helper the model never sees.
# The **first paragraph** of each docstring becomes the description the model
# gets (`Registry.register` → `inspect.getdoc(fn).split("\n\n")[0]`), which is
# why they are written for it rather than for a maintainer — and why the
# argument formats are crammed into that same paragraph instead of a tidy
# second one. Extension tools get no per-argument descriptions at all, because
# the loader has no `params` to pass, so a blank line here is the difference
# between the model knowing `when` takes ISO-8601 and it guessing.
# Pinned by test_the_model_is_told_what_format_a_reminder_time_takes.


def set_timer(minutes: float, label: str = "") -> str:
    """Set a countdown timer that notifies the user when it finishes.
    `minutes` is a number, where 0.5 means thirty seconds. `label` is an
    optional short name for what the timer is for, such as "tea" or "laundry".
    """
    try:
        length = float(minutes)
    except (TypeError, ValueError) as e:
        raise TimerError("TIMER_INVALID_DURATION") from e
    if not 0 < length <= MAX_TIMER_MINUTES:
        raise TimerError("TIMER_INVALID_DURATION")

    due_at = time.time() + length * 60
    entry = _schedule().add("timer", due_at, str(label or ""), minutes=round(length, 2))
    return f"id={entry['id']} fires_at={_format_local(due_at)} label={entry['text'] or '-'}"


def set_reminder(when: str, text: str) -> str:
    """Set a reminder that notifies the user at a specific date and time.
    `when` must be an ISO-8601 local date and time such as "2026-07-26T09:00",
    so convert whatever the user said ("tomorrow morning", "in two hours") into
    that form yourself. `text` is what to remind them about.
    """
    due_at = _parse_when(when)
    if due_at <= time.time():
        raise TimerError("REMINDER_TIME_IN_PAST")
    if not isinstance(text, str) or not text.strip():
        raise TimerError("REMINDER_TIME_INVALID")

    entry = _schedule().add("reminder", due_at, text.strip())
    return f"id={entry['id']} fires_at={_format_local(due_at)} text={entry['text']}"


def list_timers() -> str:
    """List the timers and reminders that are still waiting to go off."""
    entries = _schedule().pending()
    if not entries:
        return "none"
    return "\n".join(
        f"id={e['id']} kind={e['kind']} fires_at={_format_local(e['due_at'])} "
        f"text={e['text'] or '-'}"
        for e in entries
    )


def cancel_timer(timer_id: str) -> str:
    """Cancel a waiting timer or reminder, using the id from list_timers."""
    entry = _schedule().cancel(str(timer_id))
    return f"cancelled id={entry['id']} kind={entry['kind']}"


# -- resume ------------------------------------------------------------------
#
# The one thing that has to happen at import, and the bug that proved it.
#
# Everything above is lazy on purpose: importing IS executing (§5), so a module
# body should do as little as possible. But laziness alone loses the timers.
# The scheduler thread starts when a timer is *added*, and after a restart
# nobody adds one — the schedule is read back from disk and then nothing ever
# looks at it again. A reminder set yesterday never fires, and the only way to
# wake it is to set another timer, which is not a thing a user would think to
# do.
#
# Found by restarting the backend with a live timer during the M5.4 walk-through
# and watching the deadline pass with the entry still sitting in timers.json —
# the unit tests all drove `tick()` by hand, so they proved the firing logic and
# never the wake-up. Gotcha 23's lesson, on a different milestone.
#
# The cost when there is nothing pending is one read of a small JSON file (or
# one failed open on a fresh install) and no thread at all. Regression:
# test_a_restored_timer_wakes_the_scheduler_on_load.
_schedule()
