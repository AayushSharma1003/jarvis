"""Ollama backend: NDJSON streaming over the local HTTP API."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import ChatBackend, ChatMessage, LLMError, ModelInfo, StreamEvent, TextDelta, ToolCall


def _parse_tool_call(raw: dict[str, Any]) -> ToolCall | None:
    """One wire tool_call → ToolCall, or None if it is unusable.

    Ollama nests the useful parts under "function" and supplies its own call
    id (0.32.1 emits e.g. "call_68km2vlk"); we mint one when it doesn't, since
    the id is what correlates a result back to its request. `arguments` has
    been observed as a JSON *string* on some runtimes and an object on others,
    so both are accepted. A call without a name is dropped rather than
    invented: a nameless request cannot be routed to a tool, and guessing is
    exactly the sort of thing the permission engine exists to prevent.
    """
    fn = raw.get("function") or {}
    name = fn.get("name")
    if not isinstance(name, str) or not name:
        return None
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = None
    if not isinstance(args, dict):
        args = {}
    call_id = raw.get("id") or fn.get("id") or f"call_{uuid.uuid4().hex[:12]}"
    return ToolCall(id=str(call_id), name=name, arguments=args)


class OllamaBackend(ChatBackend):
    name = "ollama"

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        # No read timeout: token gaps on a busy 8GB machine can exceed any
        # sane fixed value. Connect/write/pool stay bounded.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
        )
        # /api/show results per model id. A model's capabilities cannot change
        # without a re-pull, and models.list runs on every UI connect, so this
        # keeps the tool-capability gate off the critical path.
        self._capabilities: dict[str, list[str]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def model_capabilities(self, model: str) -> list[str] | None:
        """Ollama reports e.g. ["completion", "tools"] (verified on 0.32.1)."""
        if model in self._capabilities:
            return self._capabilities[model]
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/show", json={"model": model}, timeout=30.0
            )
            resp.raise_for_status()
            caps = resp.json().get("capabilities")
        except (httpx.HTTPError, ValueError):
            # Unknown, not unsupported — and deliberately NOT cached, so a
            # transient blip doesn't pin a model into the wrong state for the
            # life of the process.
            return None
        if not isinstance(caps, list):
            return None
        self._capabilities[model] = caps
        return caps

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError("OLLAMA_UNREACHABLE", str(e)) from e
        models = []
        for m in resp.json().get("models", []):
            details = m.get("details") or {}
            models.append(
                ModelInfo(
                    id=m["name"],
                    parameter_size=details.get("parameter_size"),
                    size_bytes=m.get("size"),
                )
            )
        return models

    @staticmethod
    def _wire(m: ChatMessage) -> dict[str, Any]:
        """One ChatMessage in Ollama's wire shape.

        Tool results go back as {"role":"tool","name":...,"content":...} and an
        assistant turn that requested tools carries them under "tool_calls" —
        both verified against 0.32.1 in tests/manual/probe_tool_calling.py,
        where the model correctly grounded its answer in the returned result.
        """
        out: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            out["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls
            ]
        if m.tool_name:
            out["name"] = m.tool_name
        return out

    async def stream_chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload: dict[str, Any] = {
            "model": model,
            "stream": True,
            "messages": [self._wire(m) for m in messages],
        }
        if tools:
            payload["tools"] = tools
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as resp:
                if resp.status_code == 404:
                    raise LLMError("MODEL_NOT_FOUND", model)
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")[:500]
                    raise LLMError("LLM_STREAM_ERROR", f"HTTP {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError as e:
                        raise LLMError("LLM_STREAM_ERROR", f"bad NDJSON line: {line[:200]}") from e
                    if chunk.get("error"):
                        raise LLMError("LLM_STREAM_ERROR", chunk["error"])
                    message = chunk.get("message") or {}
                    delta = message.get("content", "")
                    if delta:
                        yield TextDelta(delta)
                    for call in message.get("tool_calls") or []:
                        if parsed := _parse_tool_call(call):
                            yield parsed
                    if chunk.get("done"):
                        return
        except httpx.ConnectError as e:
            raise LLMError("OLLAMA_UNREACHABLE", str(e)) from e
        except httpx.HTTPError as e:
            raise LLMError("LLM_STREAM_ERROR", str(e)) from e
