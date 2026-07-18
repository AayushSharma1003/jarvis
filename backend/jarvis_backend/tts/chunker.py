"""Sentence chunking for streamed TTS.

LLM deltas arrive token-by-token; Kokoro wants whole sentences. The chunker
accumulates deltas and emits speakable sentences as soon as they complete, so
the first sentence is synthesizing while the rest still streams — this is
where the <1.5 s first-audio budget is won.

Also strips light markdown (the LLM speaks prose, but emphasis markers and
code fences would be read aloud as punctuation soup).

The FIRST chunk additionally splits at clause boundaries (comma/semicolon/
colon): Kokoro synthesizes at only ~0.6× real time on the 8 GB M2, so waiting
for a long opening sentence blows the first-audio budget — measured 3.0 s of
synth for one 4.8 s opener. A short first clause starts playback while the
rest of the sentence synthesizes behind it.
"""

from __future__ import annotations

import re

# Sentence enders followed by whitespace/end. Won't split "3.14" (no space) but
# will split after abbreviations like "Dr. " — acceptable: a spurious TTS chunk
# boundary is a tiny pause, not an error.
_BOUNDARY = re.compile(r"[.!?…]+[\"'”’)\]]*(?:\s+|$)|\n{2,}")
# Clause enders (first chunk only). Requires trailing whitespace, so "3,14"
# (European decimals) and "1:30" never split.
_CLAUSE = re.compile(r"[,;:]\s+")

_MD_STRIP = (
    (re.compile(r"```.*?(?:```|$)", re.S), " "),  # code blocks: unspeakable
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),  # links → their text
    (re.compile(r"[*_#>|]+"), " "),
)

MIN_CHARS = 15  # merge fragments shorter than this into the next sentence
FIRST_CLAUSE_MIN_CHARS = 10  # a first clause this long is worth speaking early
FIRST_MAX_WORDS = 10  # comma-less openers: cut at a word boundary past this


def speakable(text: str) -> str:
    for pattern, repl in _MD_STRIP:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceChunker:
    def __init__(self, min_chars: int = MIN_CHARS):
        self._min_chars = min_chars
        self._buf = ""
        self._emitted = False

    def feed(self, delta: str) -> list[str]:
        """Add a stream delta; return any sentences that are now complete."""
        self._buf += delta
        out: list[str] = []
        # Latency fast-path: speak the opening clause as soon as it closes,
        # or — for long comma-less openers — the first FIRST_MAX_WORDS words.
        if not self._emitted:
            m = _CLAUSE.search(self._buf)
            sentence_first = _BOUNDARY.search(self._buf)
            if m and (sentence_first is None or m.start() < sentence_first.start()):
                clause = speakable(self._buf[: m.end()])
                if len(clause) >= FIRST_CLAUSE_MIN_CHARS:
                    out.append(clause)
                    self._buf = self._buf[m.end() :]
                    self._emitted = True
            elif sentence_first is None:
                words = self._buf.split(" ")
                if len(words) > FIRST_MAX_WORDS + 1:  # +1: last word may be mid-token
                    head = " ".join(words[:FIRST_MAX_WORDS])
                    spoken = speakable(head)
                    if spoken:
                        out.append(spoken)
                        self._buf = self._buf[len(head) :].lstrip()
                        self._emitted = True
        while True:
            m = _BOUNDARY.search(self._buf)
            if m is None:
                break
            candidate, rest = self._buf[: m.end()], self._buf[m.end() :]
            # A boundary at the very end of the buffer may still grow ("!" then
            # "?" next delta, or a decimal point mid-number): hold it back until
            # more text arrives or flush() is called.
            if not rest:
                break
            if len(speakable(candidate)) >= self._min_chars:
                spoken = speakable(candidate)
                if spoken:
                    out.append(spoken)
                    self._emitted = True
                self._buf = rest
            else:
                # Too short to speak alone — extend to the next boundary.
                nxt = _BOUNDARY.search(self._buf, m.end())
                if nxt is None or nxt.end() >= len(self._buf):
                    break  # nothing after it yet; it may still grow
                spoken = speakable(self._buf[: nxt.end()])
                if spoken:
                    out.append(spoken)
                    self._emitted = True
                self._buf = self._buf[nxt.end() :]
        return out

    def flush(self) -> str:
        """The unterminated remainder, at stream end."""
        rest = speakable(self._buf)
        self._buf = ""
        return rest
