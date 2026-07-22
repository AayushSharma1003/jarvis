"""WebSocket protocol tests with a fake LLM backend: auth, chat streaming,
persistence, history, error paths."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from jarvis_backend.config import Config
from jarvis_backend.llm.base import (
    ChatBackend,
    ChatMessage,
    LLMError,
    ModelInfo,
    TextDelta,
    ToolCall,
)
from jarvis_backend.server.app import AppState, create_app
from jarvis_backend.storage import db
from jarvis_backend.storage.conversations import Store

TOKEN = "test-token-test-token-test-token-test-token"


class FakeBackend(ChatBackend):
    name = "fake"

    def __init__(self, chunks=("Hello", " world"), fail_code=None):
        self.chunks = chunks
        self.fail_code = fail_code
        self.last_messages: list[ChatMessage] | None = None
        self.last_tools: list | None = None

    async def list_models(self):
        return [ModelInfo(id="fake:3b", parameter_size="3B")]

    async def stream_chat(self, model, messages, tools=None):
        self.last_messages = messages
        self.last_tools = tools
        if self.fail_code:
            raise LLMError(self.fail_code)
        for c in self.chunks:
            # Plain strings stay readable in the tests that only care about
            # prose; anything else is already a StreamEvent.
            yield TextDelta(c) if isinstance(c, str) else c


class StallingBackend(FakeBackend):
    """Emits one chunk then hangs, so a generation stays genuinely in flight
    until something cancels it."""

    async def stream_chat(self, model, messages, tools=None):
        self.last_messages = messages
        self.last_tools = tools
        yield TextDelta("partial")
        await asyncio.sleep(3600)


@pytest.fixture
def make_client(tmp_path):
    def _make(backend=None, registry=None, confirm=None):
        state = AppState(
            token=TOKEN,
            store=Store(db.connect(":memory:")),
            backend=backend or FakeBackend(),
            config=Config(
                ollama_url="http://unused",
                default_model="",
                config_path=tmp_path / "c.toml",
                data_dir=tmp_path,
            ),
            registry=registry,
            confirm=confirm,
        )
        if confirm is not None:
            confirm.bind(lambda: state.connections)
        return TestClient(create_app(state)), state

    return _make


@pytest.fixture
def curated(monkeypatch):
    """Treat the fake model as catalog-approved for tools.

    Without this the capability gate correctly refuses to offer tools to an
    unvetted model, which is its whole job — see test_tools_are_withheld_from
    _an_uncurated_model for the other side of it."""
    from jarvis_backend.llm import catalog

    monkeypatch.setattr(catalog, "tool_calling_ids", lambda: frozenset({"fake:3b"}))
    monkeypatch.setattr(
        "jarvis_backend.llm.capabilities.tool_calling_ids", lambda: frozenset({"fake:3b"})
    )


@contextmanager
def connect(client, token=TOKEN, consume_ready=True):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        if consume_ready:
            assert ws.receive_json()["type"] == "ready"
        yield ws


def _drain_chat(ws):
    """Collect deltas until chat.done or error; returns (deltas, done, error)."""
    deltas, done, err = [], None, None
    while done is None and err is None:
        msg = ws.receive_json()
        if msg["type"] == "chat.delta":
            deltas.append(msg["text"])
        elif msg["type"] == "chat.done":
            done = msg
        elif msg["type"] == "error":
            err = msg
    return deltas, done, err


def test_bad_token_rejected(make_client):
    client, _ = make_client()
    with connect(client, token="wrong", consume_ready=False) as ws:
        assert ws.receive_json()["code"] == "AUTH_FAILED"


def test_bad_origin_rejected(make_client):
    client, _ = make_client()
    with pytest.raises(Exception):  # handshake refused before accept  # noqa: B017
        with client.websocket_connect("/ws", headers={"Origin": "http://evil.example"}) as ws:
            ws.receive_json()


def test_good_origin_accepted(make_client):
    client, _ = make_client()
    with client.websocket_connect("/ws", headers={"Origin": "tauri://localhost"}) as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        assert ws.receive_json()["type"] == "ready"


def test_chat_roundtrip_streams_and_persists(make_client):
    client, state = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "hi there"})
        start = ws.receive_json()
        assert start["type"] == "chat.start"
        assert start["model"] == "fake:3b"

        deltas, done, err = _drain_chat(ws)
        assert err is None
        assert "".join(deltas) == "Hello world"
        assert not done["interrupted"]

        # Persisted as one atomic turn on the active path.
        turns = state.store.path(start["conversation_id"])
        assert len(turns) == 1
        assert [m.content for m in turns[0].messages] == ["hi there", "Hello world"]

        # Second message continues the conversation and sees history.
        ws.send_json(
            {"type": "chat.send", "content": "again", "conversation_id": start["conversation_id"]}
        )
        _drain_chat(ws)
        assert len(state.store.path(start["conversation_id"])) == 2


def test_system_prompt_and_history_reach_backend(make_client):
    fake = FakeBackend()
    client, _ = make_client(fake)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "question"})
        ws.receive_json()  # chat.start
        _drain_chat(ws)
    roles = [m.role for m in fake.last_messages]
    assert roles[0] == "system"
    assert roles[-1] == "user"
    assert fake.last_messages[-1].content == "question"


def test_llm_failure_before_output_sends_error_and_persists_nothing(make_client):
    client, state = make_client(FakeBackend(fail_code="OLLAMA_UNREACHABLE"))
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "hi"})
        assert ws.receive_json()["type"] == "chat.start"
        _, done, err = _drain_chat(ws)
        assert err["code"] == "OLLAMA_UNREACHABLE"
        assert done is None
    convs = state.store.list_conversations()
    assert all(state.store.path(c.id) == [] for c in convs)


def test_empty_content_rejected(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "   "})
        assert ws.receive_json()["code"] == "BAD_MESSAGE"


def test_unknown_type(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "nope"})
        assert ws.receive_json()["code"] == "UNKNOWN_TYPE"


def test_models_list(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "models.list"})
        msg = ws.receive_json()
        assert msg["type"] == "models"
        assert msg["default"] == "fake:3b"
        assert msg["models"][0]["id"] == "fake:3b"


def _tool_registry():
    from jarvis_backend.security.permissions import SAFE
    from jarvis_backend.tools.registry import Registry

    class AllowAll:
        async def check(self, name, risk, arguments, context):
            from jarvis_backend.security.permissions import Decision

            return Decision.allow()

    r = Registry(AllowAll())
    r.register(lambda: "Tuesday 21 July", risk=SAFE, name="get_datetime", description="d")
    return r


def test_tool_span_reaches_the_client(make_client, curated):
    """The UI must see tool activity as it happens, or a tool turn is just an
    unexplained pause."""
    backend = FakeBackend(chunks=[ToolCall("c1", "get_datetime", {}), TextDelta("It's Tuesday.")])
    client, _ = make_client(backend, registry=_tool_registry())
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "what day is it?"})
        assert ws.receive_json()["type"] == "chat.start"
        types = []
        span = None
        while (m := ws.receive_json())["type"] != "chat.done":
            types.append(m["type"])
            if m["type"] == "tool.span":
                span = m
        assert span is not None, types
        assert span["name"] == "get_datetime"
        assert span["ok"] is True
        assert span["content"] == "Tuesday 21 July"


def test_tools_are_offered_to_a_curated_model(make_client, curated):
    backend = FakeBackend(chunks=[TextDelta("hi")])
    client, _ = make_client(backend, registry=_tool_registry())
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "hi"})
        _drain_chat(ws)
    assert backend.last_tools is not None
    assert backend.last_tools[0]["function"]["name"] == "get_datetime"


def test_tools_are_withheld_from_an_uncurated_model(make_client):
    """The capability gate, end to end over the wire. `fake:3b` is not in the
    catalog, so it never sees a tool schema — which is what stops a model
    measured at 22% restraint from answering arithmetic with a shell command."""
    backend = FakeBackend(chunks=[TextDelta("hi")])
    client, _ = make_client(backend, registry=_tool_registry())
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "hi"})
        _drain_chat(ws)
    assert backend.last_tools is None


def test_models_list_carries_tool_support(make_client):
    """Every model entry states whether it may be handed a tool schema.

    FakeBackend inherits ChatBackend.model_capabilities, which returns None
    ("can't say"), and fake:3b isn't in the catalog — so it lands on the
    fail-safe default. A model must never arrive without this field: the
    frontend would then have to guess, and guessing wrong means offering tools
    to a model measured at 22% restraint (see docs/tool-calling.md).
    """
    client, _ = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "models.list"})
        msg = ws.receive_json()
        assert all("tools" in m for m in msg["models"])
        assert msg["models"][0]["tools"] == "optin"


def test_history_roundtrip(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "hi"})
        start = ws.receive_json()
        _drain_chat(ws)
        ws.send_json(
            {"type": "conversation.history", "conversation_id": start["conversation_id"]}
        )
        hist = ws.receive_json()
        assert hist["type"] == "history"
        assert [m["role"] for m in hist["turns"][0]["messages"]] == ["user", "assistant"]


# -- chat management (M3.5) ------------------------------------------------


def _start_conversation(ws, content="hi"):
    """Run one full exchange; returns its conversation id."""
    ws.send_json({"type": "chat.send", "content": content})
    start = ws.receive_json()
    _drain_chat(ws)
    return start["conversation_id"]


def test_conversations_list(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        cid = _start_conversation(ws, "hello there")
        ws.send_json({"type": "conversations.list"})
        msg = ws.receive_json()
        assert msg["type"] == "conversations"
        assert [c["id"] for c in msg["conversations"]] == [cid]
        # Titles are auto-set from the first message — which is why rename exists.
        assert msg["conversations"][0]["title"] == "hello there"


def test_rename_broadcasts_new_title(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        cid = _start_conversation(ws)
        ws.send_json({"type": "conversation.rename", "conversation_id": cid, "title": "Groceries"})
        msg = ws.receive_json()
        assert msg["type"] == "conversations"
        assert msg["conversations"][0]["title"] == "Groceries"


def test_rename_keeps_last_activity_order(make_client):
    """A rename must not jump a conversation to the top of the sidebar.

    The list sorts by updated_at, which is what "last activity" means here.
    Renaming an old chat is not activity; sending a message to it is.
    """
    client, _ = make_client()
    with connect(client) as ws:
        older = _start_conversation(ws, "first chat")
        newer = _start_conversation(ws, "second chat")

        ws.send_json({"type": "conversation.rename", "conversation_id": older, "title": "Renamed"})
        msg = ws.receive_json()
        assert [c["id"] for c in msg["conversations"]] == [newer, older]
        assert msg["conversations"][1]["title"] == "Renamed"

        # ...but a real turn in it does move it to the top.
        ws.send_json({"type": "chat.send", "content": "more", "conversation_id": older})
        _drain_chat(ws)
        ws.send_json({"type": "conversations.list"})
        assert [c["id"] for c in ws.receive_json()["conversations"]] == [older, newer]


def test_rename_requires_a_title(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        cid = _start_conversation(ws)
        ws.send_json({"type": "conversation.rename", "conversation_id": cid, "title": "   "})
        assert ws.receive_json()["code"] == "BAD_MESSAGE"


def test_rename_unknown_conversation(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "conversation.rename", "conversation_id": "nope", "title": "x"})
        assert ws.receive_json()["code"] == "CONVERSATION_NOT_FOUND"


def test_delete_removes_conversation_and_its_rows(make_client):
    client, state = make_client()
    with connect(client) as ws:
        cid = _start_conversation(ws)
        ws.send_json({"type": "conversation.delete", "conversation_id": cid})
        msg = ws.receive_json()
        assert msg["type"] == "conversations"
        assert msg["conversations"] == []
    # The FKs have no CASCADE: prove the children really went, not just the row.
    conn = state.store._conn
    assert conn.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 0


def test_delete_unknown_conversation(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "conversation.delete", "conversation_id": "nope"})
        assert ws.receive_json()["code"] == "CONVERSATION_NOT_FOUND"


def test_delete_requires_an_id(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        ws.send_json({"type": "conversation.delete", "conversation_id": ""})
        assert ws.receive_json()["code"] == "BAD_MESSAGE"


def test_delete_while_generating_into_it(make_client):
    """The sharp edge: run_exchange persists its turn even when cancelled, so
    the delete must stop the generation and let that write land first.

    StallingBackend keeps the generation genuinely in flight — with the plain
    FakeBackend the stream would already be finished and this would prove
    nothing about the race."""
    client, state = make_client(StallingBackend())
    with connect(client) as ws:
        # No conversation_id: the id exists only from chat.start onward, which
        # is exactly the case the connection has to learn by sniffing.
        ws.send_json({"type": "chat.send", "content": "first"})
        start = ws.receive_json()
        assert start["type"] == "chat.start"
        assert ws.receive_json()["type"] == "chat.delta"  # streaming, now stalled

        ws.send_json({"type": "conversation.delete", "conversation_id": start["conversation_id"]})
        seen = []
        while (msg := ws.receive_json())["type"] != "conversations":
            seen.append(msg["type"])
        assert msg["conversations"] == []
        # chat.done before the broadcast proves the cancelled generation's write
        # landed FIRST — had the delete gone first, that append would have hit
        # the FK constraint against a conversation that no longer existed.
        assert "chat.done" in seen
    conn = state.store._conn
    assert conn.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 0


def test_delete_leaves_other_conversations_alone(make_client):
    client, _ = make_client()
    with connect(client) as ws:
        keep = _start_conversation(ws, "keep me")
        ws.send_json({"type": "chat.send", "content": "delete me"})
        drop = ws.receive_json()["conversation_id"]
        _drain_chat(ws)
        ws.send_json({"type": "conversation.delete", "conversation_id": drop})
        msg = ws.receive_json()
        assert [c["id"] for c in msg["conversations"]] == [keep]


# -- confirmation over the wire (M4.2) --------------------------------------


class ToolOnceBackend(FakeBackend):
    """Asks for a tool on the first round, then answers in words.

    A fixed chunk list re-requests the tool on every round, which would raise a
    fresh dialog each time and make the message order untestable. This is also
    what a working model actually does.
    """

    def __init__(self, call, reply="done"):
        super().__init__()
        self.call = call
        self.reply = reply

    async def stream_chat(self, model, messages, tools=None):
        self.last_messages = messages
        self.last_tools = tools
        # "Have I already been given a result?" — true on later rounds of the
        # same exchange, false again on the next turn. Counting calls instead
        # would make a second turn silently stop asking for the tool.
        answered = any(m.role == "tool" for m in messages)
        if tools and not answered:
            yield self.call
        else:
            yield TextDelta(self.reply)


ECHO_CALL = ToolCall("c1", "echo", {"text": "hi"})


def _confirming_client(make_client, *, timeout=5.0, risk=None, call=ECHO_CALL):
    """A client whose one tool needs confirmation, wired to a real broker."""
    from jarvis_backend.security.confirm import ConfirmBroker
    from jarvis_backend.security.permissions import ASK, PermissionGate
    from jarvis_backend.tools.registry import Registry

    broker = ConfirmBroker(timeout=timeout)
    registry = Registry(PermissionGate(broker))
    registry.register(
        lambda text: f"echoed {text}", risk=risk or ASK, name="echo", description="d"
    )
    client, state = make_client(ToolOnceBackend(call), registry=registry, confirm=broker)
    return client, state, broker


def _until(ws, *types):
    """Drain until one of `types` arrives, returning (message, everything before)."""
    seen = []
    while (msg := ws.receive_json())["type"] not in types:
        seen.append(msg)
    return msg, seen


def test_confirm_request_reaches_the_client_and_an_answer_runs_the_tool(make_client, curated):
    client, _, _ = _confirming_client(make_client)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "echo hi"})
        req, _ = _until(ws, "confirm.request")
        # Everything the dialog needs to describe the call truthfully.
        assert req["name"] == "echo"
        assert req["arguments"] == {"text": "hi"}
        assert req["risk"] == "ask"
        assert req["id"]

        ws.send_json({"type": "confirm.respond", "id": req["id"], "answer": "once"})
        _, rest = _until(ws, "chat.done")
        span = next(m for m in rest if m["type"] == "tool.span")
        assert span["ok"] is True
        assert span["content"] == "echoed hi"
        assert any(m["type"] == "confirm.close" for m in rest)


def test_a_denied_call_becomes_a_failed_span(make_client, curated):
    """The user must be able to see that they refused something, and the model
    must be told so it can say so instead of inventing a result."""
    client, _, _ = _confirming_client(make_client)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "echo hi"})
        req, _ = _until(ws, "confirm.request")
        ws.send_json({"type": "confirm.respond", "id": req["id"], "answer": "deny"})
        _, rest = _until(ws, "chat.done")
        spans = [m for m in rest if m["type"] == "tool.span"]
        assert len(spans) == 1
        assert spans[0]["ok"] is False
        assert spans[0]["code"] == "TOOL_DENIED"


def test_a_forged_confirm_id_does_not_approve_anything(make_client, curated):
    """There is no message a client can send that grants permission out of
    nowhere: an answer only counts against an id the backend is waiting on."""
    client, _, _ = _confirming_client(make_client, timeout=0.2)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "echo hi"})
        _until(ws, "confirm.request")
        ws.send_json({"type": "confirm.respond", "id": "forged", "answer": "session"})
        _, rest = _until(ws, "chat.done")
        spans = [m for m in rest if m["type"] == "tool.span"]
        assert spans[0]["code"] == "TOOL_CONFIRM_TIMEOUT"


def test_chat_stop_while_a_confirm_is_pending(make_client, curated):
    """The dialog must not outlive the call it was asking about."""
    client, _, broker = _confirming_client(make_client, timeout=30.0)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "echo hi"})
        req, _ = _until(ws, "confirm.request")

        ws.send_json({"type": "chat.stop"})
        _, seen = _until(ws, "chat.done", "error")
        closes = [m for m in seen if m["type"] == "confirm.close"]
        assert closes, [m["type"] for m in seen]
        assert closes[0]["id"] == req["id"]
        assert closes[0]["reason"] == "cancelled"
    assert broker.pending_count == 0


def test_delete_while_a_confirm_is_pending(make_client, curated):
    """conversation.delete cancels the generation parked on the confirm, and
    the cancelled turn's write must still land before the rows go away."""
    client, state, broker = _confirming_client(make_client, timeout=30.0)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "echo hi"})
        start, _ = _until(ws, "chat.start")
        _until(ws, "confirm.request")

        ws.send_json(
            {"type": "conversation.delete", "conversation_id": start["conversation_id"]}
        )
        msg, before = _until(ws, "conversations")
        assert msg["conversations"] == []
        # The ordering IS the test. Both the dismissal and the cancelled turn's
        # chat.done must land BEFORE the delete broadcast — that is what proves
        # the generation was stopped and allowed to finish writing first. Assert
        # only the end state and this passes even with the guard removed, because
        # the rows are gone either way and the FK violation happens later, in a
        # task nobody is watching.
        kinds = [m["type"] for m in before]
        assert "confirm.close" in kinds, kinds
        assert "chat.done" in kinds, kinds
    conn = state.store._conn
    assert conn.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 0
    assert list(conn.execute("PRAGMA foreign_key_check")) == []
    assert broker.pending_count == 0


def test_disconnecting_mid_confirm_leaves_nothing_pending(make_client, curated):
    """Closing the window cancels the generation waiting on the dialog; a
    leaked future would hold the generation slot with it."""
    client, _, broker = _confirming_client(make_client, timeout=30.0)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "echo hi"})
        _until(ws, "confirm.request")
    assert broker.pending_count == 0


def test_every_window_sees_the_confirm_and_any_may_answer(make_client, curated):
    """gotcha 9 at the protocol level: a second window must not be able to
    silently swallow a confirmation meant for the user."""
    client, _, _ = _confirming_client(make_client)
    with connect(client) as generating, connect(client) as watcher:
        generating.send_json({"type": "chat.send", "content": "echo hi"})
        req, _ = _until(generating, "confirm.request")
        mirrored, _ = _until(watcher, "confirm.request")
        assert mirrored["id"] == req["id"]

        watcher.send_json({"type": "confirm.respond", "id": req["id"], "answer": "deny"})
        _, rest = _until(generating, "chat.done")
        spans = [m for m in rest if m["type"] == "tool.span"]
        assert spans[0]["code"] == "TOOL_DENIED"


def test_a_safe_tool_never_raises_a_confirm(make_client, curated):
    """Fatigue control: only tools that need permission may interrupt."""
    from jarvis_backend.security.permissions import SAFE

    client, _, _ = _confirming_client(make_client, risk=SAFE)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "echo hi"})
        _, seen = _until(ws, "chat.done")
        assert "confirm.request" not in [m["type"] for m in seen]


def test_a_session_grant_survives_into_the_next_turn(make_client, curated):
    """The promise "for this session" spans conversations and turns; only a
    restart forgets it (nothing is written down)."""
    client, _, _ = _confirming_client(make_client)
    with connect(client) as ws:
        ws.send_json({"type": "chat.send", "content": "echo hi"})
        req, _ = _until(ws, "confirm.request")
        ws.send_json({"type": "confirm.respond", "id": req["id"], "answer": "session"})
        _until(ws, "chat.done")

        # A brand-new conversation, same call: no dialog this time.
        ws.send_json({"type": "chat.send", "content": "echo hi again"})
        _, rest = _until(ws, "chat.done")
        assert "confirm.request" not in [m["type"] for m in rest]
        span = next(m for m in rest if m["type"] == "tool.span")
        assert span["ok"] is True
