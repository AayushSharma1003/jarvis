"""Endpointer state machine tests — synthetic probabilities, no model files."""

from __future__ import annotations

import numpy as np
import pytest

from jarvis_backend.stt.endpointing import CHUNK_MS, Endpointer, Event, State
from jarvis_backend.stt.vad import CHUNK_SAMPLES


def chunk(marker: float) -> np.ndarray:
    """A chunk whose constant value identifies it, to assert utterance bounds."""
    return np.full(CHUNK_SAMPLES, marker, dtype=np.float32)


def feed_run(ep: Endpointer, prob: float, n: int, start_marker: float = 0.0) -> list[Event]:
    return [ep.feed(chunk(start_marker + i), prob) for i in range(n)]


def n_chunks(ms: int) -> int:
    return max(1, ms // CHUNK_MS)


def test_no_speech_times_out():
    ep = Endpointer(max_wait_ms=1000)
    events = feed_run(ep, prob=0.1, n=n_chunks(1000))
    assert events[-1] == Event.TIMEOUT
    assert Event.SPEECH_START not in events
    assert ep.state == State.DONE


def test_wait_forever_when_disabled():
    ep = Endpointer(max_wait_ms=None)
    events = feed_run(ep, prob=0.1, n=2000)
    assert set(events) == {Event.NONE}


def test_basic_utterance_start_and_end():
    ep = Endpointer(min_speech_ms=96, min_silence_ms=320)
    assert Event.SPEECH_START not in feed_run(ep, 0.1, 5)
    events = feed_run(ep, 0.9, n_chunks(96))
    assert events.count(Event.SPEECH_START) == 1
    events = feed_run(ep, 0.05, n_chunks(320))
    assert events[-1] == Event.SPEECH_END
    assert ep.state == State.DONE


def test_short_blip_does_not_start():
    ep = Endpointer(min_speech_ms=96)
    feed_run(ep, 0.1, 10)
    events = feed_run(ep, 0.95, 1)  # one 32 ms click
    events += feed_run(ep, 0.1, 20)
    assert Event.SPEECH_START not in events


def test_mid_speech_pause_does_not_end():
    ep = Endpointer(min_speech_ms=64, min_silence_ms=640)
    feed_run(ep, 0.9, n_chunks(64))
    events = feed_run(ep, 0.05, n_chunks(320))  # pause shorter than min_silence
    events += feed_run(ep, 0.9, 5)
    assert Event.SPEECH_END not in events
    assert ep.state == State.SPEECH


def test_max_utterance_forces_end():
    ep = Endpointer(min_speech_ms=64, max_utterance_ms=2000)
    feed_run(ep, 0.9, n_chunks(64))
    events = feed_run(ep, 0.9, n_chunks(2000))  # never goes silent
    assert Event.SPEECH_END in events


def test_utterance_includes_pre_roll():
    pre_roll_ms = 320
    ep = Endpointer(min_speech_ms=96, min_silence_ms=320, pre_roll_ms=pre_roll_ms)
    # Silence chunks marked 1000+, speech marked 2000+.
    feed_run(ep, 0.1, 50, start_marker=1000.0)
    feed_run(ep, 0.9, n_chunks(96), start_marker=2000.0)
    feed_run(ep, 0.05, n_chunks(320), start_marker=3000.0)
    audio = ep.utterance()
    assert audio.size > 0
    # First sample must come from the pre-roll (silence-marked) region.
    assert 1000.0 <= audio[0] < 2000.0
    # And the speech itself must be present.
    assert (audio >= 2000.0).any()
    # Pre-roll is bounded: at most pre_roll + min_speech chunks precede speech.
    first_speech = int(np.argmax(audio >= 2000.0))
    assert first_speech <= (n_chunks(pre_roll_ms) + n_chunks(96)) * CHUNK_SAMPLES


def test_events_stop_after_done():
    ep = Endpointer(min_speech_ms=64, min_silence_ms=128)
    feed_run(ep, 0.9, n_chunks(64))
    feed_run(ep, 0.05, n_chunks(128))
    assert ep.state == State.DONE
    assert feed_run(ep, 0.9, 10) == [Event.NONE] * 10


def test_reset_allows_reuse():
    ep = Endpointer(min_speech_ms=64, min_silence_ms=128)
    feed_run(ep, 0.9, n_chunks(64))
    feed_run(ep, 0.05, n_chunks(128))
    ep.reset()
    assert ep.state == State.IDLE
    events = feed_run(ep, 0.9, n_chunks(64))
    assert Event.SPEECH_START in events
    assert ep.utterance().size > 0 or ep.state == State.SPEECH


@pytest.mark.parametrize("bad_ms", [0, 1])
def test_tiny_durations_clamp_to_one_chunk(bad_ms):
    ep = Endpointer(min_speech_ms=bad_ms, min_silence_ms=bad_ms)
    events = feed_run(ep, 0.9, 1)
    assert Event.SPEECH_START in events
