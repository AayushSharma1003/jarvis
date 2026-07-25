"""The extension host API: the one surface an extension may call back through.

docs/security-model.md §5, docs/extensions.md. The properties under test are the
ones that keep a badly-written (or hostile) extension from taking the sidecar
with it: an unbound host is silent rather than fatal, a notification reaches
*every* window rather than the newest one, a flood is dropped, an unserializable
payload is sanitized instead of killing the WebSocket, and `state_dir` never
hands back a path inside the extension's own folder.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jarvis_backend.config import data_dir
from jarvis_backend.extensions import host


class FakeConn:
    """Stands in for server.app.Connection: all the host uses is `send`."""

    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self._fail = fail

    async def send(self, msg: dict) -> None:
        if self._fail:
            raise ConnectionResetError("gone")
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def unbound():
    """Every test starts from a host bound to nothing, and leaves one behind.

    The host is a module-level singleton because an extension imports it by
    name — there is no injection point to hand it an instance — so the global
    has to be reset around each test or one test's binding answers another's.
    """
    host.unbind()
    yield
    host.unbind()


# -- binding ----------------------------------------------------------------


def test_notify_before_bind_is_silent_not_fatal():
    """An extension that notifies at import time runs before main.py binds.

    main.py loads extensions *before* AppState exists (the connections list is
    what the host needs), so a module body calling notify() is ordinary, not
    exceptional. Raising would turn it into EXTENSION_IMPORT_FAILED and lose
    the extension over a no-op.
    """
    assert host.notify("timers-reminders", "TIMER_FINISHED") == ""


def test_state_dir_works_before_bind(tmp_path):
    """It reads the data dir directly, so an extension may call it at import."""
    assert host.state_dir("timers-reminders").is_dir()


async def test_a_bound_host_delivers_to_the_connection():
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    notification_id = host.notify("timers-reminders", "TIMER_FINISHED", {"label": "tea"})

    await _settle()
    assert notification_id
    assert conn.sent == [
        {
            "type": "notification",
            "id": notification_id,
            "source": "timers-reminders",
            "code": "TIMER_FINISHED",
            "data": {"label": "tea"},
            "speak": False,
        }
    ]


async def test_unbind_stops_delivery():
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])
    host.unbind()

    host.notify("timers-reminders", "TIMER_FINISHED")

    await _settle()
    assert conn.sent == []


# -- the broadcast ----------------------------------------------------------


async def test_a_notification_reaches_every_connection_not_the_newest():
    """Gotcha 9, again. A reloaded webview leaves an authenticated zombie
    behind, so `connections[-1]` would hand the timer to a dead page and the
    real window would never hear it."""
    first, second, third = FakeConn(), FakeConn(), FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [first, second, third])

    host.notify("timers-reminders", "TIMER_FINISHED")

    await _settle()
    assert [len(c.sent) for c in (first, second, third)] == [1, 1, 1]


async def test_one_dead_connection_does_not_stop_the_others():
    dead, alive = FakeConn(fail=True), FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [dead, alive])

    host.notify("timers-reminders", "TIMER_FINISHED")

    await _settle()
    assert len(alive.sent) == 1


async def test_speak_travels_on_the_notification():
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    host.notify("timers-reminders", "TIMER_FINISHED", speak=True)

    await _settle()
    assert conn.sent[0]["speak"] is True


async def test_every_notification_gets_its_own_id():
    """The id is what makes `voice.say` single-use — two notifications sharing
    one would mean the second is never spoken."""
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    ids = {host.notify("timers-reminders", "TIMER_FINISHED") for _ in range(5)}

    await _settle()
    assert len(ids) == 5


# -- threads ----------------------------------------------------------------


async def test_a_notification_from_another_thread_reaches_the_loop():
    """The whole reason the host exists. An extension's scheduler is a plain
    daemon thread, and touching a WebSocket from it directly is undefined
    behaviour — the hand-off is `call_soon_threadsafe`."""
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    await asyncio.to_thread(host.notify, "timers-reminders", "TIMER_FINISHED")

    await _settle()
    assert len(conn.sent) == 1


# -- bounds -----------------------------------------------------------------


async def test_a_flood_is_dropped_after_the_limit():
    """An extension in a loop would otherwise saturate the WebSocket and the
    speaker. Same posture as MAX_ENTRIES and the shell's byte cap: bound it."""
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    accepted = [
        host.notify("timers-reminders", "TIMER_FINISHED")
        for _ in range(host.MAX_NOTIFICATIONS_PER_MINUTE + 10)
    ]

    await _settle()
    assert sum(1 for i in accepted if i) == host.MAX_NOTIFICATIONS_PER_MINUTE
    assert len(conn.sent) == host.MAX_NOTIFICATIONS_PER_MINUTE


async def test_the_rate_limit_is_global_not_per_source():
    """`source` is a string the extension chose. Limiting per-source would let
    it rotate the name and evade its own limit — security theatre. The bound
    that actually holds is the one it cannot influence."""
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    accepted = [
        host.notify(f"pretend-{i}", "TIMER_FINISHED")
        for i in range(host.MAX_NOTIFICATIONS_PER_MINUTE + 10)
    ]

    await _settle()
    assert sum(1 for i in accepted if i) == host.MAX_NOTIFICATIONS_PER_MINUTE


async def test_the_window_reopens_once_the_minute_passes(monkeypatch):
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])
    now = 1_000.0
    monkeypatch.setattr(host.time, "monotonic", lambda: now)

    for _ in range(host.MAX_NOTIFICATIONS_PER_MINUTE):
        assert host.notify("timers-reminders", "TIMER_FINISHED")
    assert host.notify("timers-reminders", "TIMER_FINISHED") == ""

    now += host.RATE_WINDOW_S + 1
    assert host.notify("timers-reminders", "TIMER_FINISHED")


# -- a payload the WebSocket can actually send ------------------------------


async def test_an_unserializable_payload_is_sanitized_not_fatal():
    """`websocket.send_json` raises on an object it cannot encode, and that
    exception surfaces in the loop with no tool call to attribute it to. A
    third-party extension handing us a numpy array must degrade to a string,
    not take the connection down."""
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    host.notify("timers-reminders", "TIMER_FINISHED", {"label": object(), "n": 3})

    await _settle()
    # The proof is that it round-trips through JSON at all.
    json.dumps(conn.sent[0])
    assert conn.sent[0]["data"]["n"] == 3


async def test_a_long_value_is_truncated():
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    host.notify("timers-reminders", "TIMER_FINISHED", {"label": "x" * 5_000})

    await _settle()
    assert len(conn.sent[0]["data"]["label"]) <= host.MAX_VALUE_CHARS


async def test_too_many_keys_are_dropped():
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    host.notify(
        "timers-reminders",
        "TIMER_FINISHED",
        {f"k{i}": i for i in range(host.MAX_DATA_KEYS + 20)},
    )

    await _settle()
    assert len(conn.sent[0]["data"]) == host.MAX_DATA_KEYS


async def test_a_non_string_code_is_refused():
    """The code is what the frontend switches on to pick wording. A non-string
    would render as a missing translation at best."""
    conn = FakeConn()
    host.bind(asyncio.get_running_loop(), lambda: [conn])

    assert host.notify("timers-reminders", None) == ""  # type: ignore[arg-type]
    assert host.notify("timers-reminders", "") == ""

    await _settle()
    assert conn.sent == []


# -- state_dir --------------------------------------------------------------


def test_state_dir_is_under_the_data_dir_and_exists():
    path = host.state_dir("timers-reminders")

    assert path.is_dir()
    assert path.is_relative_to(data_dir())


def test_state_dir_is_not_inside_the_extension_folder():
    """The trap this function exists to prevent.

    An extension writing beside its own `extension.py` changes its tree digest,
    so the next `discover()` reports `changed` and it silently stops loading —
    an extension that un-approves itself the first time it saves anything.
    """
    from jarvis_backend.config import extensions_dir

    path = host.state_dir("timers-reminders")

    assert not path.is_relative_to(extensions_dir())


def test_two_extensions_get_separate_state_dirs():
    assert host.state_dir("timers-reminders") != host.state_dir("calendar-macos")


@pytest.mark.parametrize("name", ["../escape", "a/b", "", "Timers", ".", "x" * 200])
def test_state_dir_refuses_a_name_that_is_not_an_extension_name(name):
    """Hygiene, not a boundary — an extension is unsandboxed and can write
    anywhere it likes. What this stops is a *typo* silently scribbling outside
    the state directory, and it reuses the manifest's own name rule so the
    folder is always one an extension could legitimately be called."""
    with pytest.raises(ValueError):
        host.state_dir(name)


def test_state_dir_is_created_on_demand(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "fresh"))

    path = host.state_dir("timers-reminders")

    assert path.is_dir()
    assert Path(tmp_path / "fresh") in path.parents


# -- helpers ----------------------------------------------------------------


async def _settle() -> None:
    """Let call_soon_threadsafe callbacks and the tasks they spawn run."""
    for _ in range(5):
        await asyncio.sleep(0)
