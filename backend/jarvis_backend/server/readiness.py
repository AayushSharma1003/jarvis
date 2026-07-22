"""First-run readiness: can this machine actually hold a conversation?

`jarvis doctor` answers the same question for a developer, in prose. This
answers it for the UI, so every finding is a machine-readable CODE plus data
for the placeholders — the frontend owns all wording (app/src/i18n/en.json).
The two deliberately do not share a return type: doctor's detail strings are
English and would leak straight into the app if reused here.

Statuses: "fail" blocks the thing it gates, "warn" degrades it, "ok" is fine.
`ready` means nothing is failing — voice models missing is a warning, because
text chat works without them.
"""

from __future__ import annotations

from typing import Any

from ..llm.base import LLMError
from ..llm.tiering import params_b, pick_model, ram_gb, tier_budget_b

OK, WARN, FAIL = "ok", "warn", "fail"


def _check(id_: str, status: str, code: str = "", **data: Any) -> dict[str, Any]:
    check: dict[str, Any] = {"id": id_, "status": status}
    if code:
        check["code"] = code
    if data:
        check["data"] = data
    return check


async def collect(state) -> list[dict[str, Any]]:
    """Run every gate check. Never raises: a broken check is a failing check."""
    checks = [*await _llm_checks(state), _voice_check(), _wake_check(), _mic_check()]
    return checks


async def _llm_checks(state) -> list[dict[str, Any]]:
    try:
        models = await state.backend.list_models()
    except LLMError as e:
        # Without a backend there is nothing to pick from, so only one row.
        return [_check("llm", FAIL, e.code, url=state.config.ollama_url)]
    except Exception as e:  # noqa: BLE001 - readiness reports, never crashes
        return [_check("llm", FAIL, "BACKEND_UNAVAILABLE", detail=str(e)[:200])]

    llm = _check("llm", OK, count=len(models))
    try:
        chosen = pick_model(models, state.config.default_model)
    except LLMError as e:
        return [llm, _check("model", FAIL, e.code, model=state.config.default_model)]

    gb = ram_gb()
    info = next((m for m in models if m.id == chosen), None)
    return [
        llm,
        _check(
            "model",
            OK,
            model=chosen,
            source="configured" if state.config.default_model else "auto",
            params_b=params_b(info) if info is not None else None,
            ram_gb=round(gb, 1),
            budget_b=tier_budget_b(gb),
        ),
    ]


def _voice_check() -> dict[str, Any]:
    from ..assets import missing

    absent = [a.name for a in missing(group="voice")]
    if absent:
        return _check("voice_models", WARN, "VOICE_MODELS_MISSING", models=absent)
    return _check("voice_models", OK)


def _wake_check() -> dict[str, Any]:
    from ..assets import missing

    absent = [a.name for a in missing(group="wake")]
    if absent:
        return _check("wake_models", WARN, "WAKE_MODELS_MISSING", models=absent)
    return _check("wake_models", OK)


def _mic_check() -> dict[str, Any]:
    """Is there an input device we could open?

    Note what this canNOT tell you: on macOS a *denied* microphone permission
    still enumerates devices and still opens a stream — it just delivers
    silence forever. Detecting that properly needs AVFoundation, so the UI
    pairs this row with copy pointing at Privacy settings when Jarvis never
    hears anything.
    """
    try:
        import sounddevice as sd
    except (ImportError, OSError) as e:  # OSError: PortAudio missing
        return _check("microphone", WARN, "AUDIO_RUNTIME_MISSING", detail=str(e)[:200])
    try:
        inputs = sum(1 for d in sd.query_devices() if d["max_input_channels"] > 0)
    except Exception as e:  # noqa: BLE001 - a PortAudio failure is a warning, not a crash
        return _check("microphone", WARN, "AUDIO_RUNTIME_MISSING", detail=str(e)[:200])
    if not inputs:
        return _check("microphone", WARN, "NO_INPUT_DEVICE")
    return _check("microphone", OK, count=inputs)


async def payload(state) -> dict[str, Any]:
    checks = await collect(state)
    return {
        "type": "readiness",
        "ready": not any(c["status"] == FAIL for c in checks),
        "checks": checks,
    }
