from friday.voice.chunker import SentenceStream, strip_markdown


def test_sentences_released_as_they_complete():
    stream = SentenceStream()
    assert stream.feed("Hello boss. I checked your fol") == ["Hello boss."]
    assert stream.feed("ders. Nothing new") == ["I checked your folders."]
    assert stream.flush() == ["Nothing new"]


def test_fragmented_stream_reassembles():
    stream = SentenceStream()
    out: list[str] = []
    for ch in "The report is ready. It has three sections!":
        out += stream.feed(ch)
    out += stream.flush()
    assert out == ["The report is ready.", "It has three sections!"]


def test_decimals_and_abbreviations_do_not_split():
    stream = SentenceStream()
    out = stream.feed("Pi is 3.14 e.g. roughly three. More text follows here.")
    assert out[0] == "Pi is 3.14 e.g. roughly three."


def test_short_fragments_are_held_until_flush():
    stream = SentenceStream()
    assert stream.feed("Ok.") == []
    assert stream.flush() == ["Ok."]


def test_flush_on_empty_stream():
    assert SentenceStream().flush() == []


def test_strip_markdown():
    assert strip_markdown("**bold** and `code` # header") == "bold and code  header"
