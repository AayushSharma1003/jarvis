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

from ..llm import capabilities
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
    checks = [
        *_config_checks(state),
        *await _llm_checks(state),
        _voice_check(),
        _wake_check(),
        _mic_check(getattr(state, "mic_health", None)),
    ]
    return checks


def _config_checks(state) -> list[dict[str, Any]]:
    """Nothing at all when config.toml parsed, one warning when it did not.

    The app boots either way (config.load_or_default), but an unreadable config
    costs the user their filesystem roots and their dangerous tools — failing
    closed, because a file we cannot read cannot authorise access. That is the
    right trade only if it is visible: the user hand-edited that file, they are
    the only one who can repair it, and from the inside the symptom is Jarvis
    inexplicably refusing to touch files it touched yesterday.

    A warning rather than a failure: text chat is untouched, and locking
    someone out of the whole app over a stray bracket is the exact behaviour
    this replaced.
    """
    code = getattr(state, "config_error", None)
    if not code:
        return []
    return [_check("config", WARN, code, path=str(state.config.config_path))]


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
        await _tools_check(state, chosen),
    ]


async def _tools_check(state, model: str) -> dict[str, Any]:
    """Can the chosen model be trusted with tools?

    Never a failure: tools being off costs the user actions, not conversation,
    and the same reasoning that keeps missing voice models a warning applies.
    The codes distinguish the two off states because they have different
    answers — `optin` the user can turn on and be warned about, `unsupported`
    means switching models is the only fix. See llm/capabilities.py.
    """
    if state.registry is None:
        return _check("tools", WARN, "TOOLS_DISABLED", model=model)
    try:
        caps = await state.backend.model_capabilities(model)
    except Exception:  # noqa: BLE001 - readiness reports, never crashes
        caps = None
    support = capabilities.classify(model, caps)
    if support == capabilities.ON:
        return _check("tools", OK, model=model)
    code = "TOOLS_OPTIN" if support == capabilities.OPTIN else "TOOLS_UNSUPPORTED"
    return _check("tools", WARN, code, model=model)


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


def _mic_check(health=None) -> dict[str, Any]:
    """Is there an input device, and has it ever actually been heard?

    Device enumeration alone is not an answer, and believing it was is what let
    gotcha 36 run for a day: a *denied* microphone still enumerates, still
    opens, and still delivers a stream — of exact zeros, forever. This row said
    `ok, count: 2` on a machine that was completely deaf.

    So the count is only half of it; the other half is `health`, which the
    voice exchange and the wake path write from what they actually observed
    (audio/silence.py). Deliberately NOT probed here: opening a stream would
    block on an unanswered permission prompt (gotcha 38), contend with the
    always-on wake service for the device, and pull the OS prompt in front of a
    user who may only ever type.

    `verified` distinguishes "we have heard this microphone work" from "there
    is a device and nobody has tried yet". Never a failure: a broken mic costs
    voice, not conversation — same reasoning as missing voice models.
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
    if health is not None and health.is_silent:
        return _check("microphone", WARN, "MIC_SILENT", count=inputs)
    return _check("microphone", OK, count=inputs, verified=bool(health and health.verified))


async def payload(state) -> dict[str, Any]:
    checks = await collect(state)
    return {
        "type": "readiness",
        "ready": not any(c["status"] == FAIL for c in checks),
        "checks": checks,
    }
