"""Split a streaming text response into speakable sentences.

The agent streams text in arbitrary fragments; the TTS engine wants complete
sentences. ``SentenceStream`` buffers fragments and releases sentences as soon
as they are complete, so FRIDAY starts speaking while the model is still
writing — the single biggest lever on perceived latency.
"""

from __future__ import annotations

import re

# End of sentence: terminal punctuation followed by whitespace or end-of-text.
# The lookahead keeps decimals ("3.14") intact; the lookbehind keeps
# single-letter abbreviations ("e.g.", initials) intact.
_SENTENCE_END = re.compile(r"(?<!\b[A-Za-z])([.!?…]+)(?=\s|$)")

# Markdown that reads badly aloud; voice output strips it.
_MARKDOWN_NOISE = re.compile(r"[*_`#]+")


def strip_markdown(text: str) -> str:
    return _MARKDOWN_NOISE.sub("", text)


class SentenceStream:
    def __init__(self, min_length: int = 8):
        # Very short "sentences" ("Ok." / "1.") sound choppy alone; they get
        # merged into the sentence that follows them.
        self.min_length = min_length
        self._buffer = ""

    def feed(self, fragment: str) -> list[str]:
        """Add a text fragment; return any speakable chunks now complete."""
        self._buffer += fragment
        sentences: list[str] = []
        search_from = 0
        while True:
            match = _SENTENCE_END.search(self._buffer, search_from)
            if not match:
                break
            end = match.end()
            candidate = self._buffer[:end].strip()
            if len(candidate) < self.min_length:
                # Too short to speak alone — merge with the next sentence.
                search_from = end
                continue
            sentences.append(candidate)
            self._buffer = self._buffer[end:].lstrip()
            search_from = 0
        return sentences

    def flush(self) -> list[str]:
        """Return whatever remains (end of the response)."""
        rest = self._buffer.strip()
        self._buffer = ""
        return [rest] if rest else []
