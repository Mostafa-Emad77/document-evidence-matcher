"""
Quote linker — derives description↔quote pairs directly from the parsed
document structure.

For Word-exported HTML/DOCX, quotes are already marked as `Style1` blocks.
The most reliable pairing is therefore:
  1. Group consecutive quote elements into one quote block
  2. Use the nearest preceding narration paragraph as its description
  3. Prefer an inline trailing citation in the quote block; otherwise fall back
     to the next citation anchor in the document
"""
from __future__ import annotations

import re
from backend.schemas import CitationGroup, DocumentElement, ElementType, QuoteImageLink
from backend.services.citation_text import extract_trailing_citation, is_citation_only_text

DESCRIPTION_INTRO_RE = re.compile(r"[:：]\s*$")
HEADING_LIKE_RE = re.compile(r"^(?:chapter|section|part)\b", re.IGNORECASE)


def _norm(s: str) -> str:
    return " ".join(s.split()).strip().lower()


def _nearest_image_id(
    quote_position: int,
    elements: list[DocumentElement],
) -> tuple[str | None, int | None]:
    """Find the image element closest in position to the given quote position."""
    images = [e for e in elements if e.type == ElementType.image and e.image]
    if not images:
        return None, None
    nearest = min(images, key=lambda e: abs(e.position - quote_position))
    return nearest.image.image_id, nearest.position  # type: ignore[union-attr]


def _extract_trailing_citation(text: str) -> str | None:
    return extract_trailing_citation(text)


def _is_citation_only_paragraph(text: str) -> bool:
    return is_citation_only_text(text)


def _nearest_preceding_narration(
    elements: list[DocumentElement],
    start_index: int,
) -> tuple[str, int | None]:
    for idx in range(start_index - 1, -1, -1):
        elem = elements[idx]
        if elem.type == ElementType.narration and elem.text:
            return elem.text, elem.position
    return "", None


def _nearest_preceding_description_narration(
    elements: list[DocumentElement],
    start_index: int,
) -> tuple[str, int | None]:
    """
    Strictly source descriptions from narration paragraphs that look like quote
    introductions. Never fall back to heading/title text.
    """
    for idx in range(start_index - 1, -1, -1):
        elem = elements[idx]
        if elem.type != ElementType.narration or not elem.text:
            continue
        if _looks_like_quote_description(elem.text):
            return elem.text, elem.position
    return "", None


def _looks_like_quote_description(text: str) -> bool:
    """A description usually introduces a quote and ends with a colon."""
    normalized = " ".join(text.split())
    if len(normalized) < 20:
        return False
    return bool(DESCRIPTION_INTRO_RE.search(normalized))


def _looks_like_heading_line(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if not normalized:
        return True
    if HEADING_LIKE_RE.search(normalized):
        return True
    if normalized.endswith((".", "!", "?", ";", ":")):
        return False
    words = re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", normalized)
    if len(words) < 3 or len(words) > 24:
        return False
    titled = sum(1 for w in words if w[:1].isupper())
    # Title-like lines often have high TitleCase density and no terminal punctuation.
    return (titled / max(1, len(words))) >= 0.70


def extract_quote_links(
    elements: list[DocumentElement],
    citation_groups: list[CitationGroup],
) -> list[QuoteImageLink]:
    """
    Walk the parsed document structure and build stable description↔quote pairs.
    """
    links: list[QuoteImageLink] = []
    citations_by_text = {_norm(c.citation_text): c for c in citation_groups if c.citation_text}
    index = 0
    quote_counter = 0

    while index < len(elements):
        elem = elements[index]
        if elem.type != ElementType.quote or not elem.text:
            index += 1
            continue

        block_start = index
        block: list[DocumentElement] = []
        while index < len(elements):
            current = elements[index]
            if current.type != ElementType.quote or not current.text:
                break
            block.append(current)
            index += 1

        description, description_position = _nearest_preceding_description_narration(elements, block_start)
        if not description:
            # Keep quote extraction conservative: only emit quote pairs when
            # the preceding narration explicitly introduces quoted material.
            continue

        quote_parts: list[str] = []
        inline_citation: str | None = None
        for quote_elem in block:
            text = (quote_elem.text or "").strip()
            if not text:
                continue
            if _looks_like_heading_line(text):
                continue

            # If a paragraph is citation-only, capture it as citation metadata
            # and do not include it in quote_text.
            if _is_citation_only_paragraph(text):
                inline_citation = text
                continue

            # Some source quotes end with an inline parenthetical citation in
            # the same paragraph. Capture it for citation_text and strip it
            # from quote_text so citations appear only once (below the quote).
            trailing = _extract_trailing_citation(text)
            if trailing and inline_citation is None:
                inline_citation = trailing
                trimmed = text[: -len(trailing)].rstrip(" \t\n\r,;:-")
                text = trimmed or text

            quote_parts.append(text)

        if not quote_parts:
            continue

        position = block[0].position
        nearest_img_id, img_position = _nearest_image_id(position, elements)

        next_elem = elements[index] if index < len(elements) else None
        nearest_citation = None
        if next_elem and next_elem.type == ElementType.citation:
            nearest_citation = next(
                (g for g in citation_groups if g.position == next_elem.position),
                None,
            )

        selected_citation_text = (
            inline_citation
            or (nearest_citation.citation_text if nearest_citation else None)
        )

        # Prefer image linked to the selected citation group (includes matched PDF screenshots).
        selected_group = None
        if selected_citation_text:
            selected_group = citations_by_text.get(_norm(selected_citation_text))
        if selected_group is None:
            selected_group = nearest_citation
        citation_position = selected_group.position if selected_group else None
        if selected_group and selected_group.images:
            linked_image = selected_group.images[0]
            nearest_img_id = linked_image.image_id
            img_position = linked_image.position

        quote_counter += 1
        links.append(QuoteImageLink(
            quote_id=f"quote_{quote_counter}",
            description_text=description,
            quote_text="\n\n".join(quote_parts),
            citation_text=selected_citation_text,
            description_position=description_position,
            quote_start_position=block[0].position,
            quote_end_position=block[-1].position,
            citation_position=citation_position,
            nearest_image_id=nearest_img_id,
            image_position=img_position,
        ))

    return links
