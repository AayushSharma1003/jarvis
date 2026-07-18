"""System prompt assembly. Kept short on purpose: prompt length is TTFT latency
(docs/latency.md), and small local models degrade with long instructions."""

DEFAULT_SYSTEM_PROMPT = (
    "You are JARVIS, a helpful assistant running locally on the user's computer. "
    "Be concise and direct. Answers may be spoken aloud, so prefer short sentences "
    "and avoid markdown tables or long lists unless asked."
)

# Appended for spoken exchanges. A short opening sentence is a latency feature:
# the first TTS chunk can't start until the first sentence/clause closes.
VOICE_SUFFIX = (
    " This is a spoken conversation: reply in brief conversational sentences, "
    "open with a short direct sentence, no markdown."
)


def system_prompt(conversation_override: str | None = None, voice: bool = False) -> str:
    base = conversation_override or DEFAULT_SYSTEM_PROMPT
    return base + VOICE_SUFFIX if voice else base
