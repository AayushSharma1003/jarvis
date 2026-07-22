"""May this model be handed a tool schema?

Three states, and the reason there are three:

  "unsupported" — the runtime reports that this model's chat template has no
      tool support. Handing it tools produces garbage, so this is a hard no,
      not a user preference.
  "on"          — curated in catalog/models.toml with a `tool-calling` tag:
      somebody has actually measured it with
      tests/manual/probe_tool_calling.py. Numbers in docs/tool-calling.md.
  "optin"       — the template supports tools, but this model is unvetted.
      OFF by default; the user may enable it per-model and is warned.

Why unvetted models default to OFF, which is the load-bearing decision here:

A model that calls a tool when it shouldn't manufactures permission dialogs the
user never asked for, and docs/security-model.md §"Known limitations" names
confirmation fatigue as a real attack surface. This is measured, not
theoretical — llama3.2:3b answers "what's 17 times 4?" by running `echo 17*4`
in a SHELL, and "what's the capital of France?" by fetching the web (which
under §3 would taint the conversation for the rest of the session). It scored
76%, and hardening the prompt made it *worse* (67%), so there is no cheap
prompt-side fix.

That makes "can this model decline a tool?" a security property of the model,
and unknown models get the safe answer. In the project's own words: cut the
tool list before cutting the security layer — and, it turns out, cut the model
list too.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from .base import ChatBackend, ModelInfo
from .catalog import tool_calling_ids

ToolSupport = Literal["on", "optin", "unsupported"]

ON: ToolSupport = "on"
OPTIN: ToolSupport = "optin"
UNSUPPORTED: ToolSupport = "unsupported"

# What the runtime calls the capability. Ollama's /api/show returns e.g.
# ["completion", "tools"]; verified against 0.32.1.
TOOLS_CAPABILITY = "tools"


def classify(model_id: str, capabilities: list[str] | None) -> ToolSupport:
    """Resolve one model's tool state.

    `capabilities=None` means the backend cannot tell us (an older runtime, a
    cloud adapter, a failed probe). That is NOT treated as "unsupported": we
    only claim a hard no when the runtime actually said so. Unknown falls
    through to the catalog, which answers on/optin — and optin is already off
    by default, so the fail-safe direction holds either way.
    """
    if capabilities is not None and TOOLS_CAPABILITY not in capabilities:
        return UNSUPPORTED
    return ON if model_id in tool_calling_ids() else OPTIN


async def resolve(backend: ChatBackend, models: list[ModelInfo]) -> dict[str, ToolSupport]:
    """Tool state for every model, probing the backend concurrently.

    Probes run in parallel because this sits behind `models.list`, which the UI
    sends on every connect; serially this would be one round trip per installed
    model. Adapters are expected to cache (OllamaBackend does).
    """
    caps = await asyncio.gather(
        *(backend.model_capabilities(m.id) for m in models), return_exceptions=True
    )
    return {
        m.id: classify(m.id, c if isinstance(c, list) else None)
        for m, c in zip(models, caps, strict=True)
    }
