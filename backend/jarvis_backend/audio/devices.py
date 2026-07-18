"""Audio device queries (thin sounddevice wrappers; doctor uses these too)."""

from __future__ import annotations


class AudioError(Exception):
    """Raised with a machine-readable code; the frontend translates codes."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def have_input() -> bool:
    try:
        import sounddevice as sd

        return any(d["max_input_channels"] > 0 for d in sd.query_devices())
    except Exception:  # noqa: BLE001 - a broken audio stack means "no"
        return False


def have_output() -> bool:
    try:
        import sounddevice as sd

        return any(d["max_output_channels"] > 0 for d in sd.query_devices())
    except Exception:  # noqa: BLE001
        return False
