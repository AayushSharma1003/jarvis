"""LLM backend adapter interface. New backends (cloud or local) implement this.

Contract notes for implementers:
- stream_chat yields StreamEvent objects: TextDelta for prose as it arrives,
  ToolCall when the model asks for a tool. A backend that cannot do tools just
  never yields ToolCall.
- Errors are raised as LLMError with a machine-readable code; the frontend
  translates codes to user-facing strings (never put English prose in codes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    parameter_size: str | None = None  # e.g. "3.2B" as reported by the runtime
    size_bytes: int | None = None


@dataclass(frozen=True)
class ToolCall:
    """A model's request to run a tool. `id` correlates the result back."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextDelta:
    text: str


# What stream_chat yields. Prose and tool requests arrive interleaved on one
# stream because that is how the wire delivers them.
StreamEvent = TextDelta | ToolCall


@dataclass(frozen=True)
class ChatMessage:
    role: str  # 'system' | 'user' | 'assistant' | 'tool'
    content: str
    # Set on an assistant message that requested tools, so the next round can
    # show the model what it asked for.
    tool_calls: tuple[ToolCall, ...] = ()
    # Set on a 'tool' message: which tool produced this result.
    tool_name: str | None = None


class ChatBackend(ABC):
    name: str = "base"

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    def stream_chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...

    async def model_capabilities(self, model: str) -> list[str] | None:
        """Runtime-reported capabilities, e.g. ["completion", "tools"].

        `None` means this backend cannot say — an older runtime, a cloud
        adapter with no such endpoint, or a failed probe. Callers must read
        that as "unknown", NEVER as "unsupported"; llm/capabilities.classify
        is the one place that distinction is made. Not abstract, so an adapter
        that can't answer simply inherits the honest default.
        """
        return None
