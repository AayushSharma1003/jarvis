"""LLM backend adapter interface. New backends (cloud or local) implement this.

Contract notes for implementers:
- stream_chat yields plain text deltas as they arrive.
- Errors are raised as LLMError with a machine-readable code; the frontend
  translates codes to user-facing strings (never put English prose in codes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


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
class ChatMessage:
    role: str  # 'system' | 'user' | 'assistant' | 'tool'
    content: str


class ChatBackend(ABC):
    name: str = "base"

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    def stream_chat(
        self, model: str, messages: list[ChatMessage]
    ) -> AsyncIterator[str]: ...

    async def model_capabilities(self, model: str) -> list[str] | None:
        """Runtime-reported capabilities, e.g. ["completion", "tools"].

        `None` means this backend cannot say — an older runtime, a cloud
        adapter with no such endpoint, or a failed probe. Callers must read
        that as "unknown", NEVER as "unsupported"; llm/capabilities.classify
        is the one place that distinction is made. Not abstract, so an adapter
        that can't answer simply inherits the honest default.
        """
        return None
