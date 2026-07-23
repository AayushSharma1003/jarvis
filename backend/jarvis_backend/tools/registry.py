"""The tool registry: schema generation and the single path to invocation.

Two jobs:

1. Turn a plain Python function into a JSON-schema tool definition the model
   can be shown, by introspecting its signature. Extensions reuse exactly this
   in phase 5, which is why it introspects rather than asking authors to
   hand-write schemas.
2. Be the ONLY way a tool runs, with the security gate as a constructor
   argument. docs/architecture.md: "the registry enforces this structurally;
   tools cannot opt out." A Registry cannot be built without a gate, so there
   is no code path that invokes a tool without consulting one.

Results come back as ToolResult, never exceptions: a tool that fails is a
result the model should see and react to, not a crash that ends the exchange.
Every failure carries a machine-readable code — the frontend owns the wording.
"""

from __future__ import annotations

import asyncio
import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..security.permissions import Decision, Gate, RiskLevel, ToolContext

# A tool result longer than this is truncated before it reaches the model.
# Unbounded tool output is a context-window denial of service, and on a 3B
# model a huge result also crowds out the actual question.
MAX_RESULT_CHARS = 8_000

_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


@dataclass(frozen=True)
class ToolOutput:
    """What a tool returns when its result carries untrusted content.

    Tools normally return a plain `str`. Returning this instead lets a tool
    declare that what it fetched came from somewhere the user did not write —
    a file's contents, later a web page — so the agent loop can mark the
    conversation tainted (security/taint.py, §3).

    The tool declares it because the tool is the only thing that knows: the
    registry sees an opaque return value, and the loop sees a string. Nobody
    downstream can infer "this is untrusted" from the text itself, which is
    precisely why prompt-side labeling doesn't work either.
    """

    content: str
    taint_source: str = ""


@dataclass(frozen=True)
class ToolResult:
    name: str
    call_id: str
    content: str
    ok: bool = True
    code: str = ""  # machine-readable failure code, empty when ok
    # Where untrusted content in `content` came from, "" when trusted. The
    # agent loop turns this into conversation taint; the registry only relays.
    taint_source: str = ""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    risk: RiskLevel
    fn: Callable[..., Any]
    parameters: dict[str, Any] = field(default_factory=dict)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _json_type(annotation: Any) -> tuple[str, bool]:
    """Map a type hint to a JSON-schema type. Returns (type, optional).

    `str | None` is an optional string: the model should still be told the
    type, and the None arm only says the argument may be omitted.
    """
    optional = False
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        optional = len(args) < len(typing.get_args(annotation))
        annotation = args[0] if args else str
    return _JSON_TYPES.get(annotation, "string"), optional


def build_parameters(fn: Callable[..., Any]) -> dict[str, Any]:
    """JSON schema for a function's arguments, from its signature.

    Per-argument descriptions come from the `params` mapping passed to
    register(); parsing them out of docstrings would be one more format to get
    subtly wrong, and the model only needs a sentence per argument.
    """
    hints = typing.get_type_hints(fn)
    descriptions: dict[str, str] = getattr(fn, "_param_docs", {})
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        if name == "self" or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        json_type, optional = _json_type(hints.get(name, str))
        prop: dict[str, Any] = {"type": json_type}
        if doc := descriptions.get(name):
            prop["description"] = doc
        properties[name] = prop
        if param.default is inspect.Parameter.empty and not optional:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


class Registry:
    """Holds the tools and enforces that every call passes the gate."""

    def __init__(self, gate: Gate):
        # Not optional and not defaulted: a Registry without a security gate
        # must be impossible to construct, not merely discouraged.
        self._gate = gate
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        fn: Callable[..., Any],
        *,
        risk: RiskLevel,
        name: str = "",
        description: str = "",
        params: dict[str, str] | None = None,
    ) -> Tool:
        if params:
            fn._param_docs = params  # type: ignore[attr-defined]
        tool = Tool(
            name=name or fn.__name__,
            description=description or (inspect.getdoc(fn) or "").split("\n\n")[0],
            risk=risk,
            fn=fn,
            parameters=build_parameters(fn),
        )
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """Tool definitions to send to the model."""
        return [t.schema() for t in self._tools.values()]

    async def invoke(
        self,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Run one tool call. Never raises; failures come back as ToolResult.

        `context` is passed straight through to the gate — the registry never
        reads it. A caller that omits it gets a fresh empty one, which is the
        safe direction: an empty deny-memo means more confirmations, never
        fewer. It is built per call rather than defaulted in the signature
        because ToolContext holds a mutable set.
        """
        tool = self._tools.get(name)
        if tool is None:
            # Models invent tool names, especially small ones. Say so plainly
            # so the model can correct itself on the next round.
            return ToolResult(name, call_id, "", ok=False, code="TOOL_NOT_FOUND")

        decision: Decision = await self._gate.check(
            tool.name, tool.risk, arguments, context or ToolContext()
        )
        if not decision.allowed:
            return ToolResult(name, call_id, "", ok=False, code=decision.code or "TOOL_DENIED")

        try:
            bound = inspect.signature(tool.fn).bind(**arguments)
        except TypeError:
            return ToolResult(name, call_id, "", ok=False, code="TOOL_BAD_ARGUMENTS")

        try:
            if inspect.iscoroutinefunction(tool.fn):
                output = await tool.fn(*bound.args, **bound.kwargs)
            else:
                # Tools are ordinary blocking functions; keep them off the event
                # loop so a slow one can't stall the WebSocket or the voice path.
                output = await asyncio.to_thread(tool.fn, *bound.args, **bound.kwargs)
        except Exception as e:  # noqa: BLE001 - a failing tool is a result, not a crash
            code = getattr(e, "code", "TOOL_FAILED")
            return ToolResult(name, call_id, str(e)[:500], ok=False, code=code)

        if isinstance(output, ToolOutput):
            text, taint_source = output.content, output.taint_source
        else:
            text, taint_source = (output if isinstance(output, str) else repr(output)), ""
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n… (truncated)"
        # Truncation does not clear the taint: a shortened untrusted string is
        # still untrusted, and the injection is usually in the first paragraph.
        return ToolResult(name, call_id, text, taint_source=taint_source)
