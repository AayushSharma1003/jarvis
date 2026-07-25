"""Speaking a notification: `voice.say` when no spoken turn is running (M5.4).

Through M4.2 `voice.say` only meant "add this to the sentence queue of the turn
that is currently speaking", so it silently dropped anything sent while Jarvis
was idle. A timer fires when it fires — almost never mid-turn — so the whole
announcement would have been swallowed.

The wording still never originates in the backend: the UI renders the sentence
from the notification's `code` + `data` and sends it back here, which is the
same arrangement M4.2 used for the confirm prompt. What is new is that there
may be no turn to queue it against, and that every open window receives the
same notification and would otherwise speak it once each.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from jarvis_backend.server.voice import speak_line
from tests.test_voice_ws import (
    FakeVoiceIO,
    drain_voice,
    make_voice_client,  # noqa: F401 (fixture)
    utterance_script,
)
from tests.test_ws import StallingBackend, connect


def _say(ws, text="Your tea timer is up.", notification_id=None):
    msg = {"type": "voice.say", "text": text}
    if notification_id is not None:
        msg["notification_id"] = notification_id
    ws.send_json(msg)


def _settle(ws):
    """Round-trip a message so the server has processed everything before it.

    Skips whatever a live turn is emitting in the meantime — the point is the
    barrier, not the traffic.
    """
    ws.send_json({"type": "ping"})
    while ws.receive_json()["type"] != "pong":
        pass


# -- the idle path ----------------------------------------------------------


def test_a_line_said_while_idle_is_actually_spoken(make_voice_client):  # noqa: F811
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)

    with connect(client) as ws:
        _say(ws)
        _settle(ws)

    assert io.synthesized == ["Your tea timer is up."]
    assert io.player_.enqueued


def test_a_line_said_while_idle_reaches_the_speaker(make_voice_client):  # noqa: F811
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)

    with connect(client) as ws:
        _say(ws)
        _settle(ws)

    assert len(io.player_.enqueued) == 1


def test_an_empty_line_says_nothing(make_voice_client):  # noqa: F811
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)

    with connect(client) as ws:
        _say(ws, text="   ")
        _settle(ws)

    assert io.synthesized == []


def test_a_non_string_line_says_nothing(make_voice_client):  # noqa: F811
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)

    with connect(client) as ws:
        ws.send_json({"type": "voice.say", "text": 42})
        _settle(ws)

    assert io.synthesized == []


def test_speaking_still_works_with_no_voice_io(make_voice_client):  # noqa: F811
    """A text-only build gets the toast and no speech, not a broken socket."""
    client, _ = make_voice_client(None)

    with connect(client) as ws:
        _say(ws)
        _settle(ws)  # the proof is that the connection survives


def test_a_failing_synthesizer_does_not_tear_down_the_connection(make_voice_client):  # noqa: F811
    io = FakeVoiceIO(utterance_script())

    def boom(text):
        raise RuntimeError("kokoro exploded")

    io.synthesize = boom
    client, _ = make_voice_client(io)

    with connect(client) as ws:
        _say(ws)
        _settle(ws)


# -- the live-turn path still wins ------------------------------------------


def test_a_line_said_during_a_live_turn_is_still_spoken(make_voice_client):  # noqa: F811
    """The M4.2 confirm-prompt path, unregressed.

    The backend stalls mid-stream so the turn is genuinely in flight when the
    line arrives — which is the situation the confirm prompt is sent in. Which
    of the two routes it took is asserted directly and without this much
    machinery by `test_speak_line_defers_to_a_live_turn_on_another_connection`;
    what this covers is that adding the idle route did not swallow the old one.
    """
    io = FakeVoiceIO(utterance_script())
    client, state = make_voice_client(io, backend=StallingBackend())

    with connect(client) as ws:
        ws.send_json({"type": "voice.start"})
        while not state.connections or state.connections[0].voice_sentences is None:
            ws.receive_json()

        _say(ws, text="I need your OK.")
        _settle(ws)
        assert io.synthesized == ["I need your OK."]

        ws.send_json({"type": "voice.stop"})
        drain_voice(ws)


async def test_speak_line_defers_to_a_live_turn_on_another_connection():
    """A zombie window sending `voice.say` must not synthesize behind the back
    of the window that is actually mid-turn — the queue belongs to the turn,
    not to the connection that happened to receive the notification."""
    io = FakeVoiceIO([])
    queue: asyncio.Queue = asyncio.Queue()

    class Conn:
        voice_sentences = queue

    class State:
        voice_io = io
        connections = [Conn()]

    await speak_line(State(), "Your tea timer is up.")

    assert io.synthesized == []
    assert queue.get_nowait() == "Your tea timer is up."


# -- the single-use notification id -----------------------------------------


def test_the_same_notification_is_only_spoken_once(make_voice_client):  # noqa: F811
    """Three open windows all receive the broadcast and all answer. Without a
    single-use id Jarvis says the same sentence three times, which is the
    zombie-connection failure mode gotchas 8 and 9 describe, with a speaker
    attached."""
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)

    with connect(client) as ws:
        _say(ws, notification_id="n1")
        _say(ws, notification_id="n1")
        _say(ws, notification_id="n1")
        _settle(ws)

    assert io.synthesized == ["Your tea timer is up."]


def test_two_different_notifications_are_both_spoken(make_voice_client):  # noqa: F811
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)

    with connect(client) as ws:
        _say(ws, text="First.", notification_id="n1")
        _say(ws, text="Second.", notification_id="n2")
        _settle(ws)

    assert io.synthesized == ["First.", "Second."]


def test_a_line_with_no_notification_id_is_never_deduplicated(make_voice_client):  # noqa: F811
    """The confirm prompt has no id and may legitimately repeat."""
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)

    with connect(client) as ws:
        _say(ws, text="I need your OK.")
        _say(ws, text="I need your OK.")
        _settle(ws)

    assert len(io.synthesized) == 2


def test_a_claim_from_one_window_blocks_the_other_window(make_voice_client):  # noqa: F811
    """The dedup has to live on the shared state, not per connection —
    per-connection would dedup nothing, since the double-speak comes from two
    *different* windows answering the same broadcast."""
    io = FakeVoiceIO(utterance_script())
    client, _ = make_voice_client(io)

    with connect(client) as first, connect(client) as second:
        _say(first, notification_id="n1")
        _settle(first)
        _say(second, notification_id="n1")
        _settle(second)

    assert io.synthesized == ["Your tea timer is up."]


def test_the_claim_memo_is_bounded(make_voice_client):  # noqa: F811
    """A long-running backend must not accumulate ids forever."""
    from jarvis_backend.server.app import MAX_SPOKEN_NOTIFICATIONS

    io = FakeVoiceIO(utterance_script())
    client, state = make_voice_client(io)

    with connect(client) as ws:
        for i in range(MAX_SPOKEN_NOTIFICATIONS + 25):
            _say(ws, text=f"line {i}", notification_id=f"n{i}")
        _settle(ws)

    assert len(state.spoken_notifications) <= MAX_SPOKEN_NOTIFICATIONS


# -- serialization ----------------------------------------------------------


async def test_two_notifications_do_not_interleave_into_one_stream():
    """`speak_line` shares RealVoiceIO's one cached Player, so two
    announcements arriving together must play one after the other rather than
    splicing their samples into a single stream.

    The assertion is **non-overlap**, not order: synthesis runs in a worker
    thread, so without the lock the two would genuinely be in flight at once
    and the enter/exit log would nest. Asserting a particular order instead
    would pass with the lock removed.
    """
    io = FakeVoiceIO([])
    log: list[str] = []

    def slow(text):
        log.append(f"enter {text}")
        time.sleep(0.05)  # long enough for the other to barge in if it can
        log.append(f"exit {text}")
        return np.full(240, 0.1, dtype=np.float32), 24_000

    io.synthesize = slow

    class State:
        voice_io = io
        connections: list = []

    await asyncio.gather(speak_line(State(), "one"), speak_line(State(), "two"))

    assert len(io.player_.enqueued) == 2
    # enter/exit must pair up without nesting, whichever ran first.
    depth = 0
    for event in log:
        depth += 1 if event.startswith("enter") else -1
        assert depth in (0, 1), f"overlapping synthesis: {log}"


@pytest.mark.timeout(10)
async def test_speak_line_with_no_voice_io_is_a_no_op():
    class State:
        voice_io = None
        connections: list = []

    await speak_line(State(), "anything")
