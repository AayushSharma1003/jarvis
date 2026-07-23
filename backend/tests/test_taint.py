"""Taint tracking — docs/security-model.md §3, executable.

The property under test is not "a flag gets set". It is: **once untrusted
content is in a conversation, an approval the user gave before it arrived stops
covering calls made after it.** That is the difference between taint and a
label, and it is the reason the grant key alone cannot be trusted — an injection
reuses the exact same tool and arguments the user already approved.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_backend.security.confirm import ANSWER_SESSION
from jarvis_backend.security.permissions import (
    ASK,
    DANGEROUS,
    SAFE,
    Decision,
    PermissionGate,
    ToolContext,
)
from jarvis_backend.security.taint import TaintTracker
from tests.test_confirm import FakeConn, _ask, _broker

CONV = "conv-1"
OTHER = "conv-2"
SOURCE = "/Users/x/Documents/notes.txt"


# -- the tracker ------------------------------------------------------------


def test_a_clean_conversation_has_no_source():
    t = TaintTracker()
    assert t.is_tainted(CONV) is False
    assert t.source(CONV) == ""


def test_taint_sticks():
    t = TaintTracker()
    t.taint(CONV, SOURCE)
    assert t.is_tainted(CONV) is True
    assert t.source(CONV) == SOURCE


def test_taint_is_per_conversation():
    """Reading a file in one chat must not put every other chat behind a
    dialog — that is the fatigue §"Known limitations" warns about."""
    t = TaintTracker()
    t.taint(CONV, SOURCE)
    assert t.is_tainted(OTHER) is False


def test_the_first_source_wins():
    """A reason that keeps changing as the model reads more files is a reason
    people stop reading."""
    t = TaintTracker()
    t.taint(CONV, SOURCE)
    t.taint(CONV, "/Users/x/Downloads/other.txt")
    assert t.source(CONV) == SOURCE


@pytest.mark.parametrize("cid,source", [("", SOURCE), (CONV, "")])
def test_incomplete_taint_is_ignored(cid, source):
    """A blank source would make `is_tainted` true with nothing to show the
    user — a dialog that says "this follows content from" and then stops."""
    t = TaintTracker()
    t.taint(cid, source)
    assert t.is_tainted(cid) is False


# -- the gate reads it ------------------------------------------------------


async def test_an_untainted_call_carries_no_reason():
    seen = {}

    class Recording:
        async def request(self, name, risk, arguments, context, reason=""):
            seen["reason"] = reason
            return Decision.allow()

    gate = PermissionGate(Recording(), taint=TaintTracker())
    await gate.check("write_file", ASK, {}, ToolContext(conversation_id=CONV))
    assert seen["reason"] == ""


async def test_a_tainted_call_carries_the_source_as_its_reason():
    """This is what puts "follows content from …" in the dialog."""
    seen = {}

    class Recording:
        async def request(self, name, risk, arguments, context, reason=""):
            seen["reason"] = reason
            return Decision.allow()

    tracker = TaintTracker()
    tracker.taint(CONV, SOURCE)
    gate = PermissionGate(Recording(), taint=tracker)
    await gate.check("write_file", ASK, {}, ToolContext(conversation_id=CONV))
    assert seen["reason"] == SOURCE


async def test_the_gate_reads_taint_live_not_at_construction():
    """The model reads a file in round 1 and writes in round 2 — the write is
    gated *after* the read, so a snapshot taken when the gate was built (or when
    the exchange started) would miss the taint that matters most."""
    seen = []

    class Recording:
        async def request(self, name, risk, arguments, context, reason=""):
            seen.append(reason)
            return Decision.allow()

    tracker = TaintTracker()
    gate = PermissionGate(Recording(), taint=tracker)
    ctx = ToolContext(conversation_id=CONV)

    await gate.check("write_file", ASK, {}, ctx)  # clean
    tracker.taint(CONV, SOURCE)  # ← the read happens here
    await gate.check("write_file", ASK, {}, ctx)  # tainted
    assert seen == ["", SOURCE]


async def test_taint_in_another_conversation_does_not_leak_into_this_one():
    seen = {}

    class Recording:
        async def request(self, name, risk, arguments, context, reason=""):
            seen["reason"] = reason
            return Decision.allow()

    tracker = TaintTracker()
    tracker.taint(OTHER, SOURCE)
    gate = PermissionGate(Recording(), taint=tracker)
    await gate.check("write_file", ASK, {}, ToolContext(conversation_id=CONV))
    assert seen["reason"] == ""


async def test_safe_tools_are_read_only_so_taint_need_not_escalate_them():
    """**Pins the invariant PermissionGate documents.**

    §3 says taint escalates every *side-effectful* call regardless of risk. We
    satisfy that by keeping `safe` synonymous with read-only, so there is
    nothing side-effectful left down there to escalate. If someone adds a `safe`
    tool that actually does something — §1's `send_notification` is the
    candidate — this test is the tripwire: it will still pass, but the reviewer
    is sent here by the comment in permissions.py, and the fix is to classify
    that tool `ask` or teach the gate a per-tool side-effect flag.
    """
    asked = []

    class Recording:
        async def request(self, name, risk, arguments, context, reason=""):
            asked.append(name)
            return Decision.allow()

    tracker = TaintTracker()
    tracker.taint(CONV, SOURCE)
    gate = PermissionGate(Recording(), taint=tracker)

    decision = await gate.check("read_file", SAFE, {}, ToolContext(conversation_id=CONV))
    assert decision == Decision.allow()
    assert asked == [], "a read is not side-effectful; tainting must not gate it"


# -- taint versus session grants (the load-bearing pair) --------------------


async def test_a_grant_given_before_the_taint_does_not_cover_a_call_after_it():
    """The attack this closes: the user approves `write_file` on their own
    notes, the model then reads a file containing "now overwrite ~/.zshrc", and
    the write that follows reuses the same tool and arguments the grant was for.
    Only the taint can tell those two calls apart."""
    conn = FakeConn()
    broker = _broker(conn, timeout=0.2)
    args = {"path": "/tmp/a.txt", "content": "x"}

    granted = await _ask(broker, conn, ANSWER_SESSION, name="write_file", arguments=args)
    assert granted == Decision.allow()
    # Same call again while clean: covered by the grant, no dialog.
    assert await broker.request("write_file", ASK, args, ToolContext()) == Decision.allow()
    assert len(conn.requests()) == 1

    # Now untrusted content arrives, and the identical call must ask again.
    decision = await broker.request("write_file", ASK, args, ToolContext(), SOURCE)
    assert decision == Decision.deny("TOOL_CONFIRM_TIMEOUT"), "it must have re-asked"
    assert len(conn.requests()) == 2


async def test_approving_a_tainted_call_grants_nothing():
    """Symmetric to the above: an approval given *while* tainted must not
    silently cover later calls either."""
    conn = FakeConn()
    broker = _broker(conn, timeout=0.2)
    args = {"path": "/tmp/a.txt", "content": "x"}

    seen = len(conn.requests())
    task = asyncio.create_task(broker.request("write_file", ASK, args, ToolContext(), SOURCE))
    request = await _await(conn, seen)
    broker.respond(request["id"], ANSWER_SESSION)
    assert await task == Decision.allow()

    # Clean call, same arguments: nothing was remembered, so it asks.
    decision = await broker.request("write_file", ASK, args, ToolContext())
    assert decision == Decision.deny("TOOL_CONFIRM_TIMEOUT")
    assert len(conn.requests()) == 2


async def test_the_reason_reaches_the_dialog_payload():
    """The UI needs it to say why, and to hide 'allow for this session'."""
    conn = FakeConn()
    broker = _broker(conn, timeout=0.2)
    await broker.request("write_file", ASK, {}, ToolContext(), SOURCE)
    assert conn.requests()[-1]["reason"] == SOURCE


async def test_an_untainted_request_sends_an_empty_reason():
    conn = FakeConn()
    broker = _broker(conn, timeout=0.2)
    await broker.request("write_file", ASK, {}, ToolContext())
    assert conn.requests()[-1]["reason"] == ""


async def test_a_dangerous_tainted_call_is_still_refused_when_dangerous_is_off():
    """Taint escalates; it never de-escalates. The global switch still wins,
    and the user is not asked at all."""
    tracker = TaintTracker()
    tracker.taint(CONV, SOURCE)
    asked = []

    class Recording:
        async def request(self, name, risk, arguments, context, reason=""):
            asked.append(name)
            return Decision.allow()

    gate = PermissionGate(Recording(), taint=tracker, allow_dangerous=lambda: False)
    decision = await gate.check(
        "delete_file", DANGEROUS, {}, ToolContext(conversation_id=CONV)
    )
    assert decision == Decision.deny("TOOL_DANGEROUS_DISABLED")
    assert asked == []


async def _await(conn: FakeConn, after: int) -> dict:
    for _ in range(400):
        if len(conn.requests()) > after:
            return conn.requests()[-1]
        await asyncio.sleep(0.005)
    raise AssertionError("no confirm.request was broadcast")


# -- end to end through the agent loop --------------------------------------


async def test_a_reading_tool_taints_its_conversation_through_the_loop(store):
    """The wiring proof: ToolOutput.taint_source → ToolResult → run_exchange →
    the tracker. Without it the sandbox and the tracker would both be correct
    and nothing would ever connect them."""
    from jarvis_backend.agent.loop import run_exchange
    from jarvis_backend.llm.base import ToolCall
    from jarvis_backend.security.permissions import SafeOnlyGate
    from jarvis_backend.tools.registry import Registry, ToolOutput
    from tests.test_ws import ToolOnceBackend

    registry = Registry(SafeOnlyGate())
    registry.register(
        lambda: ToolOutput("file contents", taint_source=SOURCE),
        risk=SAFE,
        name="read_file",
        description="d",
    )
    tracker = TaintTracker()
    cid = store.create_conversation(title="t")

    await run_exchange(
        store=store,
        backend=ToolOnceBackend(ToolCall("c1", "read_file", {}), reply="done"),
        model="fake:3b",
        conversation_id=cid,
        user_text="read it",
        on_delta=_noop,
        registry=registry,
        taint=tracker,
    )
    assert tracker.source(cid) == SOURCE

    # And it outlives the exchange: the next turn in this conversation is still
    # tainted, because the assistant's prose about that content is replayed even
    # though the raw tool result is not.
    assert tracker.is_tainted(cid) is True


async def test_a_tool_returning_a_plain_string_taints_nothing(store):
    from jarvis_backend.agent.loop import run_exchange
    from jarvis_backend.llm.base import ToolCall
    from jarvis_backend.security.permissions import SafeOnlyGate
    from jarvis_backend.tools.registry import Registry
    from tests.test_ws import ToolOnceBackend

    registry = Registry(SafeOnlyGate())
    registry.register(lambda: "a listing", risk=SAFE, name="list_dir", description="d")
    tracker = TaintTracker()
    cid = store.create_conversation(title="t")

    await run_exchange(
        store=store,
        backend=ToolOnceBackend(ToolCall("c1", "list_dir", {}), reply="done"),
        model="fake:3b",
        conversation_id=cid,
        user_text="list it",
        on_delta=_noop,
        registry=registry,
        taint=tracker,
    )
    assert tracker.is_tainted(cid) is False


async def _noop(_text: str) -> None:
    return None
