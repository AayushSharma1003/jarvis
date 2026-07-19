"""join_speech_segments: whisper noise annotations must not become utterances."""

from jarvis_backend.stt.transcriber import join_speech_segments


def test_plain_speech_joins():
    assert join_speech_segments([" Hello", "world. "]) == "Hello world."


def test_blank_audio_marker_is_dropped():
    assert join_speech_segments(["[BLANK_AUDIO]"]) == ""


def test_common_noise_annotations_are_dropped():
    assert join_speech_segments(["[Music]", "(wind blowing)", "♪"]) == ""


def test_mixed_segments_keep_the_speech():
    assert join_speech_segments(["[BLANK_AUDIO]", "Turn on the lights."]) == "Turn on the lights."


def test_annotation_inside_a_sentence_is_kept():
    # Only segments that are ENTIRELY an annotation are dropped.
    assert join_speech_segments(["He said (quietly) hello"]) == "He said (quietly) hello"


def test_empty_input():
    assert join_speech_segments([]) == ""
