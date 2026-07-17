"""System prompt assembly. Kept short on purpose: prompt length is TTFT latency
(docs/latency.md), and small local models degrade with long instructions."""

DEFAULT_SYSTEM_PROMPT = (
    "You are JARVIS, a helpful assistant running locally on the user's computer. "
    "Be concise and direct. Answers may be spoken aloud, so prefer short sentences "
    "and avoid markdown tables or long lists unless asked."
)


def system_prompt(conversation_override: str | None = None) -> str:
    return conversation_override or DEFAULT_SYSTEM_PROMPT
