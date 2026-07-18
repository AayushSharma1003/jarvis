"""SentenceChunker: streamed deltas → speakable sentences."""

from __future__ import annotations

from jarvis_backend.tts.chunker import SentenceChunker, speakable


def feed_all(chunker: SentenceChunker, deltas: list[str]) -> list[str]:
    out: list[str] = []
    for d in deltas:
        out.extend(chunker.feed(d))
    return out


def test_emits_sentence_as_soon_as_next_text_arrives():
    c = SentenceChunker()
    assert c.feed("The weather today is sunny.") == []  # may still grow ("..")
    assert c.feed(" It will") == ["The weather today is sunny."]


def test_streaming_token_by_token():
    c = SentenceChunker()
    text = "First sentence is here. Second one follows it. And"
    out = feed_all(c, list(text))
    assert out == ["First sentence is here.", "Second one follows it."]
    assert c.flush() == "And"


def test_flush_returns_remainder():
    c = SentenceChunker()
    c.feed("No terminal punctuation here")
    assert c.flush() == "No terminal punctuation here"
    assert c.flush() == ""


def test_short_fragments_merge_forward():
    c = SentenceChunker()
    out = feed_all(c, ["Hi! ", "How are you today? ", "Great. ", "More text follows now. ", "x"])
    # "Hi!" alone is under MIN_CHARS, so it merges with the next sentence.
    assert out[0] == "Hi! How are you today?"


def test_question_and_exclamation_boundaries():
    c = SentenceChunker()
    out = feed_all(c, ["Are you sure about that? ", "Absolutely certain of it! ", "y"])
    assert out == ["Are you sure about that?", "Absolutely certain of it!"]


def test_markdown_is_stripped_for_speech():
    assert speakable("This is **bold** and `code` and a [link](http://x).") == (
        "This is bold and code and a link."
    )
    assert speakable("```python\nprint('hi')\n```") == ""
    assert speakable("# Heading\ntext") == "Heading text"


def test_decimal_numbers_do_not_split():
    c = SentenceChunker()
    out = feed_all(c, ["The value of pi is 3.14159 which is useful. ", "Next"])
    assert out == ["The value of pi is 3.14159 which is useful."]


def test_first_clause_emits_early():
    c = SentenceChunker()
    # A long opening sentence: the first comma clause is spoken immediately,
    # long before the sentence ends.
    out = c.feed("I can help with many things, including questions about")
    assert out == ["I can help with many things,"]
    out = c.feed(" code and writing. Second sentence here. x")
    assert out == ["including questions about code and writing.", "Second sentence here."]


def test_first_clause_respects_min_length():
    c = SentenceChunker()
    assert c.feed("Yes, but there is more to say") == []  # "Yes," too short to speak alone
    assert c.flush() == "Yes, but there is more to say"


def test_clause_split_only_applies_to_first_chunk():
    c = SentenceChunker()
    out = feed_all(
        c,
        ["Opening clause goes here, then more. ", "Second, with a comma, stays whole. ", "x"],
    )
    assert out[0] == "Opening clause goes here,"
    # Later commas never split; the short " then more." tail merges forward.
    assert out[1] == "then more. Second, with a comma, stays whole."


def test_comma_less_opener_cuts_at_word_boundary():
    c = SentenceChunker()
    out = c.feed("I can help you with a wide range of topics and activities and")
    assert out == ["I can help you with a wide range of topics"]
    out = c.feed(" more. Next sentence arrives here. x")
    assert out == ["and activities and more.", "Next sentence arrives here."]


def test_paragraph_break_is_a_boundary():
    c = SentenceChunker()
    out = feed_all(c, ["A colon-terminated list follows:\n\n", "- item one text here. ", "z"])
    assert out[0] == "A colon-terminated list follows:"
