"""
Structural segment matcher — links narration/quote segments to citation groups
using inline trailing citations first, then nearest forward citation anchors.
"""
from __future__ import annotations

from backend.schemas import (
    CitationGroup,
    DocumentElement,
    ElementType,
    SegmentMatch,
)
from backend.services.citation_text import extract_trailing_citation, is_citation_only_text

_DIRECT_CITATION_SCORE = 0.92
_FORWARD_BASE_SCORE = 0.74
_FORWARD_MIN_SCORE = 0.58
_FORWARD_DECAY_PER_GAP = 0.02
_MAX_FORWARD_GAP = 1


def _norm(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _score_forward_gap(gap: int) -> float:
    return max(_FORWARD_MIN_SCORE, _FORWARD_BASE_SCORE - (_FORWARD_DECAY_PER_GAP * gap))


def _next_citation_group(
    segment: DocumentElement,
    elements: list[DocumentElement],
    citations_by_position: dict[int, CitationGroup],
) -> tuple[CitationGroup | None, int | None]:
    try:
        index = next(idx for idx, elem in enumerate(elements) if elem.position == segment.position)
    except StopIteration:
        return None, None

    if segment.type == ElementType.quote:
        block_end = index
        while (
            block_end + 1 < len(elements)
            and elements[block_end + 1].type == ElementType.quote
        ):
            block_end += 1
        next_index = block_end + 1
    else:
        next_index = index + 1

    if next_index >= len(elements):
        return None, None
    next_elem = elements[next_index]
    if next_elem.type != ElementType.citation:
        return None, None

    group = citations_by_position.get(next_elem.position)
    if group is None:
        return None, None
    anchor_position = elements[block_end].position if segment.type == ElementType.quote else segment.position
    return group, max(0, next_elem.position - anchor_position)


def _resolve_structural_group(
    segment: DocumentElement,
    elements: list[DocumentElement],
    citations_by_text: dict[str, CitationGroup],
    citations_by_position: dict[int, CitationGroup],
) -> tuple[CitationGroup | None, float | None]:
    if segment.type == ElementType.heading:
        return None, None

    text = segment.text or ""
    trailing = extract_trailing_citation(text)
    if trailing:
        direct = citations_by_text.get(_norm(trailing))
        if direct is not None:
            return direct, _DIRECT_CITATION_SCORE

    if is_citation_only_text(text):
        return None, None

    nearest, gap = _next_citation_group(segment, elements, citations_by_position)
    if nearest is None or gap is None or gap > _MAX_FORWARD_GAP:
        return None, None
    return nearest, _score_forward_gap(gap)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_segments_to_citations(
    elements: list[DocumentElement],
    citation_groups: list[CitationGroup],
) -> list[SegmentMatch]:
    """
    For every narration/quote element, find the structurally linked CitationGroup.
    Returns a list of SegmentMatch objects.
    """
    segments = [
        e for e in elements
        if e.type in (ElementType.narration, ElementType.quote, ElementType.heading) and e.text
    ]

    if not segments or not citation_groups:
        return []

    citations_by_text = {
        _norm(group.citation_text): group
        for group in citation_groups
        if group.citation_text
    }
    citations_by_position = {
        group.position: group
        for group in citation_groups
    }

    matches: list[SegmentMatch] = []
    for seg in segments:
        linked_group, confidence = _resolve_structural_group(
            seg,
            elements,
            citations_by_text,
            citations_by_position,
        )
        if linked_group is not None:
            matches.append(SegmentMatch(
                segment_id=f"seg_{seg.position}",
                segment_text=seg.text or "",
                segment_type=seg.type,
                matched_citation_id=linked_group.citation_id,
                matched_citation_text=linked_group.citation_text,
                match_score=confidence,
                matched_images=list(linked_group.images),
            ))
        else:
            matches.append(SegmentMatch(
                segment_id=f"seg_{seg.position}",
                segment_text=seg.text or "",
                segment_type=seg.type,
            ))

    return matches
