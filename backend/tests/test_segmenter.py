"""Segmenter: merge same-speaker cues, never across speakers, split long turns."""

from __future__ import annotations

from ingest.feed_transcript import parse_json_transcript, parse_vtt
from ingest.segmenter import join_text, segment_cues, split_sentences
from ingest.transcriber import Cue
from tests.conftest import FIXTURES


def _check_invariants(segs) -> None:
    assert [s.idx for s in segs] == list(range(len(segs)))
    assert all(s.end_ms >= s.start_ms >= 0 for s in segs)
    assert all(a.start_ms <= b.start_ms for a, b in zip(segs, segs[1:], strict=False))
    assert all(s.text and s.text == s.text.strip() for s in segs)


def test_merges_consecutive_same_speaker() -> None:
    cues = [Cue(0, 1000, "Hello", "A"), Cue(1000, 2000, "there.", "A"), Cue(2000, 3000, "Hi", "B")]
    segs = segment_cues(cues)
    assert [(s.speaker, s.text, s.start_ms, s.end_ms) for s in segs] == [
        ("A", "Hello there.", 0, 2000),
        ("B", "Hi", 2000, 3000),
    ]


def test_never_merges_across_speakers_even_when_tiny() -> None:
    cues = [Cue(i * 100, i * 100 + 100, f"w{i}", "A" if i % 2 else "B") for i in range(6)]
    assert len(segment_cues(cues)) == 6


def test_respects_time_limit() -> None:
    cues = [Cue(i * 10_000, (i + 1) * 10_000, f"sentence {i}.", "A") for i in range(10)]
    segs = segment_cues(cues, max_ms=45_000)
    assert all(s.end_ms - s.start_ms <= 45_000 for s in segs)
    assert len(segs) == 3  # 4 + 4 + 2 cues of 10 s
    _check_invariants(segs)


def test_respects_word_limit() -> None:
    cues = [Cue(i * 1000, (i + 1) * 1000, " ".join(["word"] * 50), "A") for i in range(5)]
    segs = segment_cues(cues, max_words=120)
    assert [len(s.text.split()) for s in segs] == [100, 100, 50]


def test_splits_long_turn_at_sentences_with_interpolated_times() -> None:
    sentence = "This is a sentence with exactly ten words in it."
    long_text = " ".join([sentence] * 30)  # 300 words, 1 turn
    segs = segment_cues([Cue(60_000, 180_000, long_text, "Host")])
    assert len(segs) >= 3
    assert all(len(s.text.split()) <= 120 for s in segs)
    assert all(s.text.endswith(".") for s in segs)  # cut at sentence ends
    assert segs[0].start_ms == 60_000 and segs[-1].end_ms == 180_000
    assert all(a.end_ms == b.start_ms for a, b in zip(segs, segs[1:], strict=False))
    assert all(s.speaker == "Host" for s in segs)
    assert all(s.end_ms - s.start_ms <= 45_000 for s in segs)
    _check_invariants(segs)


def test_long_sentence_without_punctuation_split_on_words() -> None:
    segs = segment_cues([Cue(0, 10_000, " ".join(["um"] * 250), None)])
    assert len(segs) == 3 and all(len(s.text.split()) <= 120 for s in segs)
    assert sum(len(s.text.split()) for s in segs) == 250


def test_drops_empty_and_sorts() -> None:
    segs = segment_cues(
        [Cue(5000, 6000, "b", "A"), Cue(0, 1000, "  ", "A"), Cue(0, 1000, "a", "B")]
    )
    assert [s.text for s in segs] == ["a", "b"]


def test_join_text_punctuation() -> None:
    assert join_text(["Hello", ",", "world", "!", " (", "aside", ")"]) == "Hello, world! (aside)"
    assert split_sentences("One. Two? Three! four") == ["One.", "Two?", "Three!", "four"]


def test_word_level_json_becomes_readable_segments() -> None:
    segs = segment_cues(parse_json_transcript((FIXTURES / "transcript_buzzcast.json").read_bytes()))
    assert segs[0].text.startswith("Have you guys seen this new podcast discovery app?")
    assert segs[0].speaker == "Jordan"
    assert {"Jordan", "Alban"} <= {s.speaker for s in segs}
    _check_invariants(segs)


def test_recorded_vtt_segments() -> None:
    cues = parse_vtt((FIXTURES / "transcript_buzzcast.vtt").read_text())
    segs = segment_cues(cues)
    assert len(segs) < len(cues)
    assert all(len(s.text.split()) <= 120 and s.end_ms - s.start_ms <= 45_000 for s in segs)
    _check_invariants(segs)
