"""WebSocket protocol tests with a fake LLM backend: auth, chat streaming,
persistence, history, error paths."""

from __future__ import annotations

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
