"""The confirmation broker: how a tool call asks the user, and waits.

One `request()` call is one dialog. It broadcasts a `confirm.request` carrying a
correlation id the BACKEND generated, then waits for a `confirm.respond` naming
that id. Everything about the shape of this is a security property, not a
convenience:

- **The backend requests; the client never asserts.** There is no message a
  client can send that grants permission out of nowhere — an answer is only
  meaningful against an id the backend minted and is still waiting on. Unknown
  ids are dropped in silence.
- **Absence of an answer is a deny.** No UI connected, every send failed, the
  last window disconnected, the timeout elapsed, the broker itself broke — all
  deny. There is no path where "we couldn't ask" becomes "go ahead".
- **Broadcast, first answer wins.** Never `connections[-1]`: webview reloads
  leave authenticated zombie connections behind and a diagnostic client must not
  be able to silently steal a confirmation from the real window (gotcha 9). A
  second window answering after the first is normal traffic, not an error.
- **Grants live in memory only.** Nothing about "allow for this session" is
  written to disk, ever. Restarting the backend forgets everything, which is the
  guarantee the phrase makes.

Cancellation is the subtle part. When `chat.stop`, `voice.stop`, or a
`conversation.delete` cancels the generation, the task is parked in `await
future` here. The dismissal must still reach the UIs or the dialog outlives the
call it was asking about — and a dialog answering for a call that is already
gone is exactly how a user gets trained to click Allow. It is fired as an
independent task (`_close_soon`) because awaiting a send inside a cancellation
handler is not reliable: the very next await can re-raise.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import Any

from ..server import protocol
from .permissions import DANGEROUS, Decision, RiskLevel, ToolContext

# Long enough to read a shell command and think about it; short enough that a
# user who walked away doesn't strand the generation slot forever. Overridable
# for manual verification — the packaged app never sets it.
CONFIRM_TIMEOUT_S = 120.0

# Session grants are bounded so a long-running backend can't accumulate an
# unbounded allowlist in memory. Least-recently-used is evicted.
MAX_SESSION_GRANTS = 64

ANSWER_DENY = "deny"
ANSWER_ONCE = "once"
ANSWER_SESSION = "session"
VALID_ANSWERS = frozenset({ANSWER_DENY, ANSWER_ONCE, ANSWER_SESSION})


def timeout_s() -> float:
    """The confirm timeout, honouring JARVIS_CONFIRM_TIMEOUT_S if it parses."""
    raw = os.environ.get("JARVIS_CONFIRM_TIMEOUT_S")
    if not raw:
        return CONFIRM_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return CONFIRM_TIMEOUT_S
    return value if value > 0 else CONFIRM_TIMEOUT_S


def grant_key(name: str, arguments: dict[str, Any]) -> str:
    """The identity of "this exact call", for session grants and deny memos.

    Canonical JSON with sorted keys, so `{"a":1,"b":2}` and `{"b":2,"a":1}` are
    one key — and so `git status` and `git status; curl x | sh` are firmly two.
    docs/security-model.md §1 allows skipping confirmation for *exact-match*
    previously-approved commands only; this function is that "exact".
    """
    try:
        args = json.dumps(arguments, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        # Unorderable or exotic keys: fall back to something stable rather than
        # raising inside the gate. A weird key is its own key, never a match.
        args = repr(sorted(arguments.items(), key=repr))
    return f"{name}\x00{args}"


class ConfirmBroker:
    """Async request/response over the WebSocket fan-out, with grants."""

    def __init__(self, *, timeout: float | None = None) -> None:
        self._timeout = timeout if timeout is not None else timeout_s()
        # Set by bind(); until then there are no UIs, so everything denies.
        self._connections: Callable[[], Iterable[Any]] = tuple
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._grants: OrderedDict[str, None] = OrderedDict()

    def bind(self, connections: Callable[[], Iterable[Any]]) -> None:
        """Point the broker at the live connection list.

        Wired after AppState exists, exactly like main.py wires the wake
        service — the broker needs the connections and the connections need an
        app that owns the broker.
        """
        self._connections = connections

    # -- the ask ------------------------------------------------------------

    async def request(
        self,
        name: str,
        risk: RiskLevel,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> Decision:
        key = grant_key(name, arguments)

        if key in self._grants:
            # Re-approving something already approved this session is the
            # fatigue this grant exists to prevent. Refresh its recency so a
            # tool the user keeps using isn't evicted by one they used once.
            self._grants.move_to_end(key)
            return Decision.allow()

        if key in context.denied:
            # Already refused in this exchange. Silently deny rather than ask
            # again: the answer is known and the second dialog is the attack.
            return Decision.deny("TOOL_DENIED")

        conns = list(self._connections())
        if not conns:
            return Decision.deny("TOOL_CONFIRM_NO_UI")

        confirm_id = uuid.uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[confirm_id] = future

        request = protocol.confirm_request(
            confirm_id=confirm_id,
            name=name,
            risk=risk,
            arguments=arguments,
            conversation_id=context.conversation_id,
            voice=context.voice,
        )
        if not await self._broadcast(request, conns):
            # Every UI we knew about failed to receive it. Nobody can answer.
            self._pending.pop(confirm_id, None)
            return Decision.deny("TOOL_CONFIRM_NO_UI")

        try:
            answer = await asyncio.wait_for(future, self._timeout)
        except TimeoutError:
            await self._finish(confirm_id, "timeout")
            return Decision.deny("TOOL_CONFIRM_TIMEOUT")
        except asyncio.CancelledError:
            # The generation is being torn down. Take the dialog with it.
            await self._finish(confirm_id, "cancelled")
            raise
        await self._finish(confirm_id, "answered")

        if answer == ANSWER_DENY:
            context.denied.add(key)
            return Decision.deny("TOOL_DENIED")
        if answer == ANSWER_SESSION and risk != DANGEROUS:
            # §1: dangerous is "per-call confirmation", and per-call means
            # per-call. Enforced here rather than by hiding the button, because
            # the button is in a webview and this is not.
            self._remember(key)
        return Decision.allow()

    # -- the answer ---------------------------------------------------------

    def respond(self, confirm_id: Any, answer: Any) -> None:
        """Deliver a client's answer. Unknown or late ids are dropped."""
        if not isinstance(confirm_id, str):
            return
        future = self._pending.get(confirm_id)
        if future is None or future.done():
            # Unknown id, or a second window answering one that's already
            # settled. Both are ordinary; neither is an error worth surfacing.
            return
        # An answer we don't understand is not an approval.
        future.set_result(answer if answer in VALID_ANSWERS else ANSWER_DENY)

    @property
    def pending_count(self) -> int:
        """How many confirmations are still waiting for an answer.

        Every pending confirm belongs to some connection's generation task, and
        a disconnect cancels that task (server/app.py's `finally`), so this must
        fall back to zero on its own. It is exposed so the tests can prove that
        rather than assume it — a broker that leaks futures leaks the
        generation slot with them.
        """
        return len(self._pending)

    # -- internals ----------------------------------------------------------

    def _remember(self, key: str) -> None:
        self._grants[key] = None
        self._grants.move_to_end(key)
        while len(self._grants) > MAX_SESSION_GRANTS:
            self._grants.popitem(last=False)

    async def _finish(self, confirm_id: str, reason: str) -> None:
        """Retire a confirmation and tell every UI to dismiss its dialog.

        Awaited, including from the cancellation handler. Firing it as an
        independent task loses the race: `chat.done` for the cancelled turn goes
        out first and the dialog flickers on screen after the turn it belonged
        to is already gone. Awaiting inside an `except CancelledError` block is
        sound — the cancellation has already been delivered, and it is exactly
        how run_voice_exchange sends its final voice.state on barge-in.
        """
        self._pending.pop(confirm_id, None)
        with contextlib.suppress(Exception):
            await self._broadcast(protocol.confirm_close(confirm_id, reason), None)

    async def _broadcast(self, message: dict[str, Any], conns: list[Any] | None) -> bool:
        """Send to every connection. True if at least one received it."""
        delivered = False
        for conn in conns if conns is not None else list(self._connections()):
            with contextlib.suppress(Exception):
                await conn.send(message)
                delivered = True
        return delivered
