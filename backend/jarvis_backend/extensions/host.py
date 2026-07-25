"""The host API: the one surface an extension is invited to call back through.

docs/security-model.md §5, docs/extensions.md. M5.4.

**Why this exists.** The extension API through M5.2 is strictly request/response:
the model calls a declared function, the function returns a string, done. A
timer cannot be written against that, because a timer's whole job happens
*later* — with no model call in flight and nothing waiting on a return value.
`notify()` is that missing egress, and it is deliberately the only one.

**What it is not.** This is a convenience, not a capability boundary, and
nothing here is a security control in the sense §2 or §4 are. An approved
extension is arbitrary Python in this process (§5 leads with that): it could
already reach `state.connections` by importing the server module, or open a
socket, or write anywhere on disk. Adding a sanctioned front door does not
narrow what an extension *can* do — it gives the honest ones a stable one, so
they are not coupled to internals that move. The bounds below (rate limit,
payload sanitising, name validation) are about a *badly written* extension not
taking the sidecar down with it, which is a reliability property.

**Why a module-level singleton**, when nothing else in this codebase is one:
an extension imports this by name (`from jarvis_backend.extensions.host import
notify`) and there is no injection point to hand it an instance — the loader
imports a file, it does not construct anything. `bind()` is called from main.py
once AppState exists, exactly like `confirm.bind(...)` beside it. Until then
`notify()` is a **silent no-op**, which is the correct reading rather than a
lax one: main.py loads extensions *before* the connection list exists, so an
extension notifying from its module body is ordinary, and raising would cost
the user the whole extension over a message nobody could have received anyway.

`state_dir()` is the other half, and it exists to prevent one specific footgun.
An extension that saves state beside its own `extension.py` changes its tree
digest (approvals.py), so the next `discover()` reports `changed` and it stops
loading — an extension that un-approves itself the first time it writes
anything. There has to be somewhere legitimate to put a file, and this is it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..config import data_dir
from ..server import protocol
from .manifest import NAME_RE

log = logging.getLogger(__name__)

# A notification is one toast and (sometimes) one spoken sentence, so the useful
# rate is very low and the harmful one starts early. Bounded like everything
# else that crosses a trust line here — MAX_ENTRIES, the 64KB shell cap, the
# 512KB fetch cap — rather than trusted to be reasonable.
MAX_NOTIFICATIONS_PER_MINUTE = 10
RATE_WINDOW_S = 60.0

# `data` is display payload (a timer's label), never wording. Both caps exist
# because it reaches a React component and a TTS engine: an extension that
# passes its whole log line should produce a clipped toast, not a wall of text
# read aloud.
MAX_DATA_KEYS = 12
MAX_VALUE_CHARS = 200

# The directory every extension's private state lives under. Sibling of
# `extensions/`, not a child: a child would be inside the tree that gets hashed.
STATE_DIRNAME = "extension-state"


class _Host:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connections: Callable[[], Iterable[Any]] = tuple
        self._recent: deque[float] = deque()

    def bind(
        self, loop: asyncio.AbstractEventLoop, connections: Callable[[], Iterable[Any]]
    ) -> None:
        self._loop = loop
        self._connections = connections

    def unbind(self) -> None:
        self._loop = None
        self._connections = tuple
        self._recent.clear()

    def notify(self, source: str, code: str, data: dict[str, Any] | None, speak: bool) -> str:
        if not isinstance(code, str) or not code:
            # The frontend switches on this to choose wording; anything else is
            # a guaranteed missing translation. Refuse at the boundary.
            log.warning("extension %s sent a notification with no code", source)
            return ""
        loop = self._loop
        if loop is None:
            return ""
        if not self._allow():
            log.warning("notification from %s dropped: rate limit", source)
            return ""

        notification_id = uuid.uuid4().hex[:12]
        message = protocol.notification(
            notification_id=notification_id,
            source=str(source)[:MAX_VALUE_CHARS],
            code=code,
            data=_clean(data),
            speak=bool(speak),
        )
        # The hand-off. Extension tools run under `asyncio.to_thread` and a
        # scheduler is a plain daemon thread, so this is almost never the loop
        # thread; `call_soon_threadsafe` is correct from either.
        with contextlib.suppress(RuntimeError):  # loop already closed
            loop.call_soon_threadsafe(self._dispatch, message)
        return notification_id

    def _allow(self) -> bool:
        """Global sliding window — deliberately not per-source.

        `source` is a string the extension chose and nothing verifies it, so a
        per-source budget is evaded by rotating the name. The limit that holds
        is the one the caller cannot influence.
        """
        now = time.monotonic()
        while self._recent and now - self._recent[0] > RATE_WINDOW_S:
            self._recent.popleft()
        if len(self._recent) >= MAX_NOTIFICATIONS_PER_MINUTE:
            return False
        self._recent.append(now)
        return True

    def _dispatch(self, message: dict[str, Any]) -> None:
        """On the loop thread now. Fan out without blocking the caller."""
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._broadcast(message))

    async def _broadcast(self, message: dict[str, Any]) -> None:
        """Every connection, never the newest one (gotcha 9): a reloaded webview
        leaves an authenticated zombie behind, and a timer handed only to it is
        a timer the user never hears. A send that fails is a page that is gone."""
        for conn in list(self._connections()):
            with contextlib.suppress(Exception):
                await conn.send(message)


_HOST = _Host()


def bind(loop: asyncio.AbstractEventLoop, connections: Callable[[], Iterable[Any]]) -> None:
    """Point the host at the running loop and the live connection list."""
    _HOST.bind(loop, connections)


def unbind() -> None:
    """Drop the binding. Used by tests, and by nothing in production."""
    _HOST.unbind()


def notify(
    source: str, code: str, data: dict[str, Any] | None = None, *, speak: bool = False
) -> str:
    """Tell the user something happened. Returns the notification id, or "".

    Safe to call from any thread, and safe to call when nothing is listening.

    `code` is machine-readable and the frontend owns the wording (the i18n rule
    in CLAUDE.md); `data` carries values to interpolate — a timer's label is
    content the *user* supplied, not English this process authored. `speak=True`
    asks the UI to have Jarvis say it out loud, which it does by sending the
    sentence it rendered back as `voice.say`, so the wording still never
    originates here.

    An empty return means the notification was dropped — unbound host, no code,
    or the rate limit. Callers are not expected to care; there is nothing useful
    an extension could do about it.
    """
    return _HOST.notify(source, code, data, speak)


def state_dir(name: str) -> Path:
    """A private, writable directory for one extension. Created on demand.

    **Never write inside your own extension folder.** Those bytes are what the
    approval is keyed on (approvals.py `tree_digest`), so saving a file there
    changes the digest, flips the extension to `changed`, and stops it loading
    on the next start. This directory is a sibling of `extensions/`, outside
    every hashed tree, and under the data dir — which main.py already excludes
    from the filesystem sandbox, so no file tool can reach it either.

    The name is validated against the manifest's own rule. That is hygiene, not
    a boundary: an extension runs unsandboxed and can write wherever it wants.
    What it stops is a typo quietly scribbling outside the state directory.
    """
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f"not a valid extension name: {name!r}")
    path = data_dir() / STATE_DIRNAME / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clean(data: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce a payload into something `send_json` can definitely encode.

    `websocket.send_json` raises on a value it cannot serialize, and that lands
    in the loop with no tool call to attribute it to — a third-party extension
    passing a numpy array would take the connection down. Degrade to `repr`
    instead, which is exactly what the registry does with a non-str tool return.
    """
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in data.items():
        if len(out) >= MAX_DATA_KEYS:
            break
        if not isinstance(key, str):
            continue
        if isinstance(value, bool | int | float) or value is None:
            out[key] = value
        else:
            out[key] = (value if isinstance(value, str) else repr(value))[:MAX_VALUE_CHARS]
    return out
