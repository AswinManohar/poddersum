"""Citation validator (agent.citations): pure, no I/O."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.citations import (
    MAX_CITATIONS_PER_CLAIM,
    QUOTE_MAX_CHARS,
    ModelClaim,
    ground_claim,
    ground_claims,
    quote_for,
    segments_by_id,
)
from agent.transcript_context import SegmentRef
from api.schemas import VerdictSection

SEGMENTS = segments_by_id(
    [
        SegmentRef(11, 0, 15_000, "Ana", "Welcome to the show."),
        SegmentRef(12, 15_000, 42_000, "Bob", "Charge more in the first year."),
        SegmentRef(13, 42_000, 75_000, None, "We tested three price points."),
        SegmentRef(14, 75_000, 128_000, "Bob", "word " * 100),
    ]
)


def _claim(text: str = "A claim.", ids: list[int] | None = None, section: str | None = None):
    return ModelClaim(text=text, segment_ids=[12] if ids is None else ids, section=section)


def test_valid_claim_grounded_from_stored_segment() -> None:
    claim = ground_claim(_claim("  Founders undercharge.  ", [12]), SEGMENTS)
    assert claim is not None
    assert claim.text == "Founders undercharge."
    (cite,) = claim.citations
    assert (cite.segment_id, cite.start_ms, cite.end_ms, cite.speaker) == (
        12,
        15_000,
        42_000,
        "Bob",
    )
    assert cite.quote == "Charge more in the first year."
    assert claim.section is None


def test_invalid_segment_id_dropped_from_citations_and_claim() -> None:
    claim = ground_claim(_claim(ids=[999, 13]), SEGMENTS)
    assert claim is not None and [c.segment_id for c in claim.citations] == [13]
    assert ground_claim(_claim(ids=[999, 1000]), SEGMENTS) is None


@pytest.mark.parametrize("ids", [[], [0], [-1]])
def test_missing_or_empty_segment_ids_dropped(ids: list[int]) -> None:
    assert ground_claim(_claim(ids=ids), SEGMENTS) is None


def test_missing_segment_ids_field_is_not_a_model_claim() -> None:
    with pytest.raises(ValidationError):
        ModelClaim.model_validate({"text": "no ids"})


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_text_dropped(text: str) -> None:
    assert ground_claim(_claim(text=text), SEGMENTS) is None


def test_duplicates_removed_and_order_kept() -> None:
    claim = ground_claim(_claim(ids=[13, 12, 13, 12]), SEGMENTS)
    assert claim is not None
    assert [c.segment_id for c in claim.citations] == [13, 12]


def test_at_most_three_citations_first_ones_kept() -> None:
    claim = ground_claim(_claim(ids=[14, 13, 12, 11]), SEGMENTS)
    assert claim is not None
    assert len(claim.citations) == MAX_CITATIONS_PER_CLAIM == 3
    assert [c.segment_id for c in claim.citations] == [14, 13, 12]


def test_model_timestamps_and_quotes_are_ignored() -> None:
    raw = ModelClaim.model_validate(
        {
            "text": "Price points were tested.",
            "segment_ids": [13],
            "start_ms": 1,
            "end_ms": 2,
            "speaker": "Mallory",
            "quote": "made up",
        }
    )
    claim = ground_claim(raw, SEGMENTS)
    assert claim is not None
    cite = claim.citations[0]
    assert (cite.start_ms, cite.end_ms, cite.speaker) == (42_000, 75_000, None)
    assert cite.quote == "We tested three price points."


def test_long_segment_quote_is_cut_excerpt_of_stored_text() -> None:
    claim = ground_claim(_claim(ids=[14]), SEGMENTS)
    assert claim is not None
    quote = claim.citations[0].quote
    assert quote is not None and quote.endswith("…")
    assert len(quote) <= QUOTE_MAX_CHARS + 1
    assert SEGMENTS[14].text.startswith(quote.removesuffix("…"))
    assert quote_for("   ") is None


def test_sections_required_and_validated_for_verdict() -> None:
    ok = ground_claim(_claim(section="whats_new"), SEGMENTS, VerdictSection)
    assert ok is not None and ok.section == "whats_new"
    assert ground_claim(_claim(section=None), SEGMENTS, VerdictSection) is None
    assert ground_claim(_claim(section="best_minute"), SEGMENTS, VerdictSection) is None


def test_section_discarded_without_sections() -> None:
    claim = ground_claim(_claim(section="whats_new"), SEGMENTS)
    assert claim is not None and claim.section is None


def test_ground_claims_keeps_order_and_drops_invalid() -> None:
    claims = ground_claims(
        [_claim("one", [11]), _claim("bad", [999]), _claim("", [12]), _claim("two", [12])],
        SEGMENTS,
    )
    assert [c.text for c in claims] == ["one", "two"]
