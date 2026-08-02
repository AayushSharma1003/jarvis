"""Tell a dead microphone apart from a quiet room.

A microphone the OS has revoked -- or one muted in hardware -- does not fail in
any way a program can see. The stream opens, PortAudio's callback fires on
schedule, no status flag is set, and every sample is exactly `0.0`. Forever.
The listening window then ends in the same no-speech timeout a silent room
produces, and the user is told "didn't catch anything, try again", which can
never work. That is gotcha 36, and it cost a day of blaming permissions.

The discriminator is exactness, not loudness. A live microphone in a soundproof
room still returns room tone, its own self-noise, and the ADC's dither; the odds
of a genuine capture being bit-exact zero across thousands of samples are nil.
So the test is `== 0.0` on every sample, never "RMS below some epsilon" -- an
epsilon would have to be tuned against real rooms and would start guessing.

Hardware mute produces the same signature and is deliberately not distinguished:
the user still cannot be heard and still needs telling. The frontend owns that
wording (i18n) and can name both causes; the backend only reports the fact.
"""

from __future__ import annotations

import numpy as np


def is_digital_silence(chunk: np.ndarray) -> bool:
    """True if every sample is exactly zero -- i.e. nothing is reaching us."""
    return not np.any(chunk)


class SilenceWatch:
    """Accumulates the question "has this stream delivered anything at all?".

    Deliberately not a rolling window: a single non-zero sample anywhere is
    proof the microphone is live, and one proof is enough for the whole
    listening window. `heard_something` latches and never un-latches.
    """

    __slots__ = ("_heard", "_chunks")

    def __init__(self) -> None:
        self._heard = False
        self._chunks = 0

    def feed(self, chunk: np.ndarray) -> None:
        self._chunks += 1
        if not self._heard and not is_digital_silence(chunk):
            self._heard = True

    @property
    def heard_something(self) -> bool:
        return self._heard

    @property
    def is_dead(self) -> bool:
        """Every chunk so far was digital silence, and there were some.

        The `_chunks` guard matters: a window that read nothing at all (an
        immediate stop, a capture that never yielded) has proved nothing about
        the microphone and must not be reported as broken.
        """
        return self._chunks > 0 and not self._heard
