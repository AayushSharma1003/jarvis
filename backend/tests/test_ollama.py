"""Ollama adapter against a mock transport — no live server needed."""

import json

import httpx
import pytest

from jarvis_backend.llm.base import ChatMessage, LLMError, TextDelta, ToolCall
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
    assert out == [TextDelta("Hel"), TextDelta("lo")]


async def test_stream_chat_yields_tool_calls():
    """Verified shape against real Ollama 0.32.1: tool_calls arrive in stream
    mode, nested under "function", with the runtime supplying a call id."""

    def handler(request):
        body = _ndjson(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "function": {
                                "name": "get_datetime",
                                "arguments": {"tz": "local"},
                            },
                        }
                    ],
                },
                "done": True,
            }
        )
        return httpx.Response(200, content=body)

    out = [d async for d in _backend(handler).stream_chat("m", [ChatMessage("user", "hi")])]
    assert out == [ToolCall(id="call_abc123", name="get_datetime", arguments={"tz": "local"})]


async def test_tool_call_arguments_may_arrive_as_a_json_string():
    """Some runtimes send `arguments` as an encoded string rather than an
    object; both must land as a dict or the registry can't bind them."""

    def handler(request):
        body = _ndjson(
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "get_datetime", "arguments": '{"tz":"utc"}'}}
                    ]
                },
                "done": True,
            }
        )
        return httpx.Response(200, content=body)

    out = [d async for d in _backend(handler).stream_chat("m", [ChatMessage("user", "hi")])]
    assert out[0].arguments == {"tz": "utc"}
    assert out[0].id, "a call with no runtime id still needs one to correlate its result"


async def test_nameless_tool_call_is_dropped():
    """A call with no name cannot be routed to a tool, and guessing is exactly
    what the permission engine exists to prevent."""

    def handler(request):
        body = _ndjson(
            {"message": {"content": "hi", "tool_calls": [{"function": {}}]}, "done": True}
        )
        return httpx.Response(200, content=body)

    out = [d async for d in _backend(handler).stream_chat("m", [ChatMessage("user", "hi")])]
    assert out == [TextDelta("hi")]


async def test_tools_and_tool_results_reach_the_wire():
    """The tools schema is sent, and a tool result goes back with its name."""
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_ndjson({"message": {"content": "ok"}, "done": True}))

    schema = [{"type": "function", "function": {"name": "get_datetime"}}]
    messages = [
        ChatMessage("user", "when?"),
        ChatMessage(
            "assistant", "", tool_calls=(ToolCall("c1", "get_datetime", {}),)
        ),
        ChatMessage("tool", "Tuesday", tool_name="get_datetime"),
    ]
    async for _ in _backend(handler).stream_chat("m", messages, tools=schema):
        pass
    assert seen["tools"] == schema
    assert seen["messages"][1]["tool_calls"][0]["function"]["name"] == "get_datetime"
    assert seen["messages"][2] == {
        "role": "tool",
        "content": "Tuesday",
        "name": "get_datetime",
    }


async def test_no_tools_key_when_none_offered():
    """Sending an empty tools list still changes the prompt template; omit it."""
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_ndjson({"message": {"content": "ok"}, "done": True}))

    async for _ in _backend(handler).stream_chat("m", [ChatMessage("user", "hi")]):
        pass
    assert "tools" not in seen


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
