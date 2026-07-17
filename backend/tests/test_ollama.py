"""Ollama adapter against a mock transport — no live server needed."""

import json

import httpx
import pytest

from jarvis_backend.llm.base import ChatMessage, LLMError
from jarvis_backend.llm.ollama import OllamaBackend


def _backend(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaBackend("http://test", client=client)


def _ndjson(*chunks):
    return "\n".join(json.dumps(c) for c in chunks)


async def test_stream_chat_yields_deltas():
    def handler(request):
        assert request.url.path == "/api/chat"
        body = _ndjson(
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo"}, "done": False},
            {"message": {"content": ""}, "done": True},
        )
        return httpx.Response(200, content=body)

    b = _backend(handler)
    out = [d async for d in b.stream_chat("m", [ChatMessage("user", "hi")])]
    assert out == ["Hel", "lo"]


async def test_model_not_found():
    b = _backend(lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(LLMError) as e:
        async for _ in b.stream_chat("nope", [ChatMessage("user", "hi")]):
            pass
    assert e.value.code == "MODEL_NOT_FOUND"


async def test_inline_error_chunk():
    b = _backend(lambda r: httpx.Response(200, content=json.dumps({"error": "boom"})))
    with pytest.raises(LLMError) as e:
        async for _ in b.stream_chat("m", [ChatMessage("user", "hi")]):
            pass
    assert e.value.code == "LLM_STREAM_ERROR"


async def test_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    b = _backend(handler)
    with pytest.raises(LLMError) as e:
        await b.list_models()
    assert e.value.code == "OLLAMA_UNREACHABLE"


async def test_list_models_parses_details():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "llama3.2:3b", "size": 2000, "details": {"parameter_size": "3.2B"}}
                ]
            },
        )

    b = _backend(handler)
    models = await b.list_models()
    assert models[0].id == "llama3.2:3b"
    assert models[0].parameter_size == "3.2B"
