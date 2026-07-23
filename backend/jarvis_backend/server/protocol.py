"""WebSocket message protocol. JSON objects with a `type` discriminator.

Client → server: auth, chat.send, chat.stop, models.list, conversations.list,
                 conversation.history, ping, voice.start, voice.stop, wake.set,
                 confirm.respond, voice.say
Server → client: ready, chat.start, chat.delta, chat.done, models,
                 conversations, history, error, pong, tool.span,
                 voice.state, stt.text, voice.level, wake.status, wake.detected,
                 confirm.request, confirm.close

Errors carry machine-readable codes only; the frontend owns the wording (i18n).

Voice states: loading → listening → transcribing → thinking → speaking → idle.
The LLM reply inside a voice exchange reuses chat.start/delta/done so the chat
transcript renders identically for typed and spoken turns.
"""

from __future__ import annotations

from typing import Any


def error(code: str, detail: str = "") -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "error", "code": code}
    if detail:
        msg["detail"] = detail
    return msg


def ready(version: str) -> dict[str, Any]:
    return {"type": "ready", "version": version}


def chat_start(conversation_id: str, model: str) -> dict[str, Any]:
    return {"type": "chat.start", "conversation_id": conversation_id, "model": model}


def chat_delta(text: str) -> dict[str, Any]:
    return {"type": "chat.delta", "text": text}


def chat_done(conversation_id: str, turn_id: str, interrupted: bool = False) -> dict[str, Any]:
    return {
        "type": "chat.done",
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "interrupted": interrupted,
    }


def tool_span(span: Any) -> dict[str, Any]:
    """One tool call and its outcome, sent as it happens so the transcript can
    show activity rather than an unexplained pause.

    `content` is deliberately included: the user is entitled to see what the
    assistant was actually told, especially once tainted content can steer it.
    `code` is machine-readable — the frontend owns the wording.
    """
    return {
        "type": "tool.span",
        "call_id": span.call_id,
        "name": span.name,
        "arguments": span.arguments,
        "content": span.content,
        "ok": span.ok,
        "code": span.code,
    }


def confirm_request(
    *,
    confirm_id: str,
    name: str,
    risk: str,
    arguments: dict[str, Any],
    conversation_id: str = "",
    voice: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """Ask every open UI to confirm one tool call.

    `id` is the correlation id the backend minted; only an answer naming it
    counts, and only once (security/confirm.py). `risk` travels so the dialog
    can withhold "allow for this session" on a dangerous tool — the backend
    refuses to honour it there regardless, but the button shouldn't lie.
    `voice` tells the UI a spoken turn is waiting, so it can ask the backend to
    say so out loud; the wording is the frontend's, per the i18n rule.

    `reason` is the taint source (§3) — where the untrusted content came from,
    e.g. the path of a file that was read. It is **data, not a code**: the
    sentence around it lives in the frontend's `confirm.taintReason`, the same
    way readiness sends `model` and lets the UI write the copy. Non-empty also
    means the call is not grantable, so the UI hides "allow for this session".
    """
    return {
        "type": "confirm.request",
        "id": confirm_id,
        "name": name,
        "risk": risk,
        "arguments": arguments,
        "conversation_id": conversation_id,
        "voice": voice,
        "reason": reason,
    }


def confirm_close(confirm_id: str, reason: str) -> dict[str, Any]:
    """Dismiss a dialog nobody needs answered any more.

    Sent when the confirmation was answered (so the *other* windows close
    theirs), timed out, or was cancelled with its generation. A dialog that
    outlives its call is how users learn to click Allow without reading.
    """
    return {"type": "confirm.close", "id": confirm_id, "reason": reason}


def voice_state(state: str, reason: str = "") -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "voice.state", "state": state}
    if reason:
        msg["reason"] = reason
    return msg


def stt_text(text: str) -> dict[str, Any]:
    return {"type": "stt.text", "text": text}


def voice_level(level: float) -> dict[str, Any]:
    return {"type": "voice.level", "level": round(level, 3)}


def wake_status(enabled: bool, available: bool) -> dict[str, Any]:
    return {"type": "wake.status", "enabled": enabled, "available": available}


def wake_detected() -> dict[str, Any]:
    return {"type": "wake.detected"}
