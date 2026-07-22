"""Catch tool calls the model prints as prose instead of emitting properly.

Small models do this. Measured on llama3.2:3b, 4 leaks in 33 calls
(docs/tool-calling.md):

    {"name":"run_command","parameters\\":{\\"command":"git status"}}

Left alone that text streams into the transcript as the assistant's answer
*and* gets handed to tts/chunker.py, so Kokoro reads raw JSON aloud. It is also
a silent failure: the tool never ran, so whatever the user asked for did not
happen and nothing said so.

The filter withholds a delta stream that has begun to look like a printed tool
call, and either drops it (confirmed) or releases it intact (turned out to be
ordinary text). Releasing is the default for anything ambiguous — swallowing a
real answer is far worse than letting one ugly blob through.

**False positives are guarded by requiring a REGISTERED tool name.** A user who
asks "show me a JSON object with a name field" gets their answer: the object
only looks like a tool call if the `name` value is a tool this build actually
has. Without that check, any JSON reply would be at risk.
"""

from __future__ import annotations

import json
import re

# Enough text to decide. A printed tool call declares its name almost
# immediately; genuine prose starting with "{" has usually revealed itself
# well before this.
DECISION_WINDOW = 240

# Cheap pre-filter: a JSON object whose first key is one a tool call would use.
_LOOKS_LIKE_CALL = re.compile(
    r'^\s*\{\s*"(name|function|tool_name|tool|parameters|arguments)"\s*:', re.IGNORECASE
)
_NAME_VALUE = re.compile(r'"(?:name|tool_name|tool)"\s*:\s*"([^"]+)"', re.IGNORECASE)


class MalformedToolCallFilter:
    """Streaming filter. feed() returns text safe to emit; flush() the rest."""

    def __init__(self, tool_names: set[str] | None = None):
        self._tools = tool_names or set()
        self._buf = ""
        self._holding = False
        self._settled = False  # True once we've decided to pass everything
        self.dropped: list[str] = []

    def feed(self, delta: str) -> str:
        if self._settled:
            return delta
        self._buf += delta
        if not self._holding:
            stripped = self._buf.lstrip()
            if not stripped:
                return ""  # only whitespace so far; nothing to decide on
            if not stripped.startswith("{"):
                return self._release()
            self._holding = True
        # Holding. Decide as soon as we can, so latency isn't paid for nothing.
        if len(self._buf) >= DECISION_WINDOW:
            return "" if self._is_tool_call() else self._release()
        return ""

    def flush(self) -> str:
        """Whatever is left at end of stream."""
        if self._settled or not self._buf:
            out, self._buf = self._buf, ""
            return out
        if self._is_tool_call():
            self.dropped.append(self._buf)
            self._buf = ""
            return ""
        return self._release()

    def _release(self) -> str:
        """Give up on suspicion and pass everything through from here on."""
        self._settled = True
        self._holding = False
        out, self._buf = self._buf, ""
        return out

    def _is_tool_call(self) -> bool:
        """Does the buffer name a tool this build actually has?

        Both a strict parse and a regex probe, because the leaked text is
        frequently invalid JSON — that is *why* it leaked instead of being
        emitted through the tool channel.
        """
        if not _LOOKS_LIKE_CALL.match(self._buf):
            return False
        candidates: set[str] = set()
        try:
            parsed = json.loads(self._buf)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("name", "tool_name", "tool"):
                if isinstance(value := parsed.get(key), str):
                    candidates.add(value)
            if isinstance(fn := parsed.get("function"), dict) and isinstance(
                fn.get("name"), str
            ):
                candidates.add(fn["name"])
        candidates.update(_NAME_VALUE.findall(self._buf))
        return bool(candidates & self._tools)
