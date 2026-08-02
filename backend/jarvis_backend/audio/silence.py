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


class MicHealth:
    """What the microphone has actually been observed to do, process-wide.

    `system.readiness` used to answer "is there a microphone?" by enumerating
    devices, and reported `microphone: ok` for the entire gotcha-36 outage on a
    machine that could not hear at all. A denied device still enumerates.

    It cannot fix that by opening a stream itself, and the reasons are worth
    keeping: `Pa_OpenStream` blocks on an unanswered permission prompt and
    would hang the readiness call (gotcha 38); it would contend with the
    always-on wake service for the device; and probing at startup would drag
    the OS permission prompt out of the context that explains it, in front of a
    user who may only ever type.

    So nothing probes. The components that legitimately hold the microphone --
    the voice exchange and the wake service -- report what they saw, and
    readiness reads the verdict. UNKNOWN is honest and common: a text-only user
    who never triggers voice has told us nothing about their microphone, and
    guessing would be how this bug started.
    """

    UNKNOWN, LIVE, SILENT = "unknown", "live", "silent"

    __slots__ = ("state",)

    def __init__(self) -> None:
        self.state = self.UNKNOWN

    def heard_audio(self) -> None:
        self.state = self.LIVE

    def heard_only_silence(self) -> None:
        self.state = self.SILENT

    @property
    def is_silent(self) -> bool:
        return self.state == self.SILENT

    @property
    def verified(self) -> bool:
        return self.state == self.LIVE
