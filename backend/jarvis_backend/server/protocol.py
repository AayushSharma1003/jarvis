"""WebSocket message protocol. JSON objects with a `type` discriminator.

Client → server: auth, chat.send, chat.stop, models.list, conversations.list,
                 conversation.history, conversation.branch, ping, voice.start,
                 voice.stop, wake.set, confirm.respond, voice.say,
                 extensions.list, extensions.approve, extensions.revoke
Server → client: ready, chat.start, chat.delta, chat.done, models,
                 conversations, history, error, pong, tool.span,
                 voice.state, stt.text, voice.level, wake.status, wake.detected,
                 confirm.request, confirm.close, extensions, notification

Errors carry machine-readable codes only; the frontend owns the wording (i18n).

Voice states: loading → listening → transcribing → thinking → speaking → idle.
The LLM reply inside a voice exchange reuses chat.start/delta/done so the chat
transcript renders identically for typed and spoken turns.
"""

from __future__ import annotations

from typing import Any

from ..storage.conversations import ACTIVE_LEAF


def parent_turn_from(msg: dict[str, Any]) -> Any:
    """Which turn a `chat.send` forks from — and the absent/null distinction.

    Three cases the wire has to keep apart (storage/conversations.py):

        key absent          extend the live branch  → ACTIVE_LEAF
        "parent_turn_id": null   a root turn        → None
        "parent_turn_id": "…"    fork from there    → the id

    `msg.get("parent_turn_id")` collapses the first two, which is how an
    ordinary second message briefly became a root sibling instead of continuing
    the conversation (caught by test_chat_roundtrip_streams_and_persists). The
    explicit null is what makes *editing the first message* expressible at all,
    so the two cannot be merged back together.
    """
    if "parent_turn_id" not in msg:
        return ACTIVE_LEAF
    value = msg["parent_turn_id"]
    return value if isinstance(value, str) and value else None


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


def extensions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything the approval panel needs, as data and codes only (M5.2).

    §5 says the dialog shows **declared permissions**, so the declarations have
    to travel: description, platforms, `os`, `network`, and every tool. What
    does NOT travel is prose — `status` and `code` are machine-readable and the
    frontend owns their wording (`extension.status.*`, `extension.code.*`).

    Each tool's `risk` is the **effective** level the core will register it at,
    not the level the manifest asked for. Under `network = true` a declared
    `safe` becomes `ask`, and a panel showing `safe` there would tell the user
    the opposite of what happens.

    `digest` is the identity of the bytes currently on disk. It goes out so the
    client can echo it back on approve, which is how "approve what you were
    shown" is enforced against a folder that changed in between — the same
    backend-mints/client-echoes shape as a confirm correlation id.
    """
    return {"type": "extensions", "extensions": rows}


def notification(
    notification_id: str,
    source: str,
    code: str,
    data: dict[str, Any],
    speak: bool,
) -> dict[str, Any]:
    """Something happened that the user should be told about, right now (M5.4).

    Unlike every other server→client message this one is not the answer to
    anything: an extension's timer fires with no request in flight, which is the
    whole reason extensions/host.py exists.

    `code` + `data` rather than a sentence, per the i18n rule — the frontend
    owns the wording and interpolates the values. `data` is display payload the
    user supplied (a timer's label), never English this process authored.

    `speak` asks the UI to have Jarvis say it. The UI answers by sending the
    sentence it rendered back as `voice.say`, so the words still never
    originate in the backend. `id` is what makes that single-use: the message
    goes to every open window, and without it three windows would speak the
    same line three times.
    """
    return {
        "type": "notification",
        "id": notification_id,
        "source": source,
        "code": code,
        "data": data,
        "speak": speak,
    }


def history(
    conversation_id: str, turns: list[Any], siblings: dict[str, list[str]]
) -> dict[str, Any]:
    """The active root→leaf path, and what else could have been there (M5.5).

    `siblings` is the whole alternative set per turn, oldest first, **including
    the turn itself** — so the client derives both "2 of 3" and which id the
    arrows point at from one array, and its rule for showing the switcher is a
    plain `length > 1` with no empty case to handle.

    Sending the set rather than a count is what makes the arrows work at all: a
    count says a branch exists, an array says where it is.
    """
    return {
        "type": "history",
        "conversation_id": conversation_id,
        "turns": [
            {
                "id": t.id,
                "parent_turn_id": t.parent_turn_id,
                "siblings": siblings.get(t.id, [t.id]),
                "messages": [
                    {"id": m.id, "role": m.role, "content": m.content} for m in t.messages
                ],
            }
            for t in turns
        ],
    }


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


def assets_progress(name: str, done: int, total: int) -> dict[str, Any]:
    """Bytes, not English: the frontend formats and names the model group."""
    return {"type": "assets.progress", "name": name, "done": done, "total": total}


def assets_done(failed: list[str]) -> dict[str, Any]:
    return {"type": "assets.done", "ok": not failed, "failed": failed}


def wake_detected() -> dict[str, Any]:
    return {"type": "wake.detected"}
