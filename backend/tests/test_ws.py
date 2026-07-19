"""WebSocket protocol tests with a fake LLM backend: auth, chat streaming,
persistence, history, error paths."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from jarvis_backend.config import Config
from jarvis_backend.llm.base import ChatBackend, ChatMessage, LLMError, ModelInfo
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

    async def list_models(self):
        return [ModelInfo(id="fake:3b", parameter_size="3B")]

    async def stream_chat(self, model, messages):
        self.last_messages = messages
        if self.fail_code:
            raise LLMError(self.fail_code)
        for c in self.chunks:
            yield c


class StallingBackend(FakeBackend):
    """Emits one chunk then hangs, so a generation stays genuinely in flight
    until something cancels it."""

    async def stream_chat(self, model, messages):
        self.last_messages = messages
        yield "partial"
        await asyncio.sleep(3600)


@pytest.fixture
def make_client(tmp_path):
    def _make(backend=None):
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
        )
        return TestClient(create_app(state)), state

    return _make


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
