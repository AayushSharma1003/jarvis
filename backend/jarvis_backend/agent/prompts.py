"""System prompt assembly. Kept short on purpose: prompt length is TTFT latency
(docs/latency.md), and small local models degrade with long instructions.

That last point is measured, not folklore: hardening the tool instructions on
llama3.2:3b made its tool discipline WORSE (76% → 67%, docs/tool-calling.md).
Resist the urge to add paragraphs here.
"""

_BASE = (
    "You are JARVIS, a helpful assistant running locally on the user's computer. "
    "Be concise and direct. Answers may be spoken aloud, so prefer short sentences "
    "and avoid markdown tables or long lists unless asked. "
)

# Used when NO tools are offered — which is the common case, since only models
# curated in catalog/models.toml are given a tool schema (llm/capabilities.py).
# Without this line small models confidently claim to have started playlists
# and timers.
_NO_TOOLS = (
    "You have no tools yet: you cannot play media, set timers, open apps, browse "
    "the web, or act on this computer. Never say you did or started such an "
    "action; say you cannot do it yet, then help with words instead."
)

# Used when tools ARE offered. Deliberately one sentence: the model can already
# see the tool schemas, so it does not need to be told they exist — it needs to
# be stopped from claiming actions beyond them. Saying "you have no tools" here
# would be a lie that suppresses the tools we just handed over.
_WITH_TOOLS = (
    "Use a tool when the request needs one. You can only do what your tools "
    "allow — never claim an action you did not actually take."
)

DEFAULT_SYSTEM_PROMPT = _BASE + _NO_TOOLS

# Appended for spoken exchanges. A short opening sentence is a latency feature:
# the first TTS chunk can't start until the first sentence/clause closes.
VOICE_SUFFIX = (
    " This is a spoken conversation: reply in brief conversational sentences, "
    "open with a short direct sentence, no markdown."
)


def system_prompt(
    conversation_override: str | None = None,
    voice: bool = False,
    has_tools: bool = False,
) -> str:
    base = conversation_override or (_BASE + (_WITH_TOOLS if has_tools else _NO_TOOLS))
    return base + VOICE_SUFFIX if voice else base
