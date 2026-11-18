"""Citation validator: turns what the model wrote into contract ``Claim`` objects.

Pure functions, no I/O. The model only proposes claim text and segment ids; everything
a listener relies on comes from the stored transcript:

* Segment ids that are not segments of this episode are removed; duplicates are removed;
  at most ``MAX_CITATIONS_PER_CLAIM`` are kept (the first ones the model gave).
* ``start_ms``, ``end_ms`` and ``speaker`` are always copied from the stored segment.
  Any timestamps the model may have added are ignored (the schema has no field for them).
* ``quote`` is an excerpt of the stored segment text, never model-written text.
* A claim with empty text, or with no valid citation left, is dropped.
* When ``sections`` is given (verdict), a claim must carry one of its values, else it is
  dropped. Without ``sections`` the model's section, if any, is discarded.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import BaseModel

from agent.transcript_context import SegmentRef
from api.schemas import Citation, Claim

log = logging.getLogger(__name__)

MAX_CITATIONS_PER_CLAIM = 3
QUOTE_MAX_CHARS = 160


class ModelClaim(BaseModel):
    """One claim as the model writes it (unvalidated). Unknown fields are ignored."""

    text: str
    segment_ids: list[int]
    section: str | None = None


def quote_for(text: str, max_chars: int = QUOTE_MAX_CHARS) -> str | None:
    """A short excerpt of a segment: the text itself, or its start cut at a word boundary."""
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0] or text[:max_chars]
    return cut + "…"


def citation_for(segment: SegmentRef) -> Citation:
    return Citation(
        segment_id=segment.id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        speaker=segment.speaker,
        quote=quote_for(segment.text),
    )


def ground_claim(
    raw: ModelClaim,
    segments: Mapping[int, SegmentRef],
    sections: type[StrEnum] | None = None,
) -> Claim | None:
    """The validated claim, or ``None`` when it must be dropped (see module docstring).

    ``segments`` maps ``segments.id`` to the episode's segments."""
    text = raw.text.strip()
    if not text:
        log.info("dropped claim: empty text")
        return None

    section: str | None = None
    if sections is not None:
        if raw.section not in {s.value for s in sections}:
            log.info("dropped claim: invalid section %r", raw.section)
            return None
        section = raw.section

    ids: list[int] = []
    for segment_id in raw.segment_ids:
        if segment_id in segments and segment_id not in ids:
            ids.append(segment_id)
    if not ids:
        log.info("dropped claim: no valid citation (model gave %s)", raw.segment_ids)
        return None

    citations = [citation_for(segments[i]) for i in ids[:MAX_CITATIONS_PER_CLAIM]]
    return Claim(text=text, citations=citations, section=section)


def ground_claims(
    raws: Iterable[ModelClaim],
    segments: Mapping[int, SegmentRef],
    sections: type[StrEnum] | None = None,
) -> list[Claim]:
    """All claims that survive validation, in the model's order."""
    claims = (ground_claim(raw, segments, sections) for raw in raws)
    return [c for c in claims if c is not None]


def segments_by_id(segments: Iterable[SegmentRef]) -> dict[int, SegmentRef]:
    return {s.id: s for s in segments}
