"""
Citation grouper — walks the ordered DocumentElement list and groups
images under the citation anchor that follows them in the document.

Pattern:
  [image] [image] ... [citation]  →  CitationGroup(images=[...], citation=...)
"""
from __future__ import annotations

import re
from typing import Optional

from backend.schemas import CitationGroup, DocumentElement, ElementType, ExtractedImage

# Extracts title / outlet / date from the raw citation string
_TITLE_RE = re.compile(r"['\u2018\u201c](?P<title>[^'\u2019\u201d]+)['\u2019\u201d]")
_URL_RE = re.compile(r"https?://\S+")


def _parse_citation(text: str) -> dict:
    """Best-effort parse of a parenthetical citation string."""
    # Extract URL if present
    url_match = _URL_RE.search(text)
    url = url_match.group(0) if url_match else None

    # Remove URL from text before further parsing
    clean = _URL_RE.sub("", text).strip(" ()\n")

    # Extract quoted title
    title_match = _TITLE_RE.search(clean)
    source_title: Optional[str] = title_match.group("title").strip() if title_match else None

    # Remove title from clean text
    if title_match:
        clean = clean[title_match.end():].strip(" ,")

    # Remaining parts: outlet, date — split by comma
    parts = [p.strip() for p in clean.split(",") if p.strip()]
    outlet = parts[0] if len(parts) > 0 else None
    date = parts[-1] if len(parts) > 1 else None

    return {
        "source_title": source_title,
        "outlet": outlet,
        "date": date,
        "url": url,
    }


def build_citation_groups(elements: list[DocumentElement]) -> list[CitationGroup]:
    """
    Scan elements in order.  Accumulate consecutive image elements.
    When a citation element is encountered, flush the accumulated images
    into a new CitationGroup anchored to that citation.
    """
    groups: list[CitationGroup] = []
    pending_images: list[ExtractedImage] = []
    citation_counter = 0

    for elem in elements:
        if elem.type == ElementType.image and elem.image:
            pending_images.append(elem.image)

        elif elem.type == ElementType.citation and elem.text:
            citation_counter += 1
            parsed = _parse_citation(elem.text)
            group = CitationGroup(
                citation_id=f"citation_{citation_counter}",
                citation_text=elem.text,
                position=elem.position,
                images=list(pending_images),
                **parsed,
            )
            groups.append(group)
            pending_images = []  # reset for next group

        else:
            # Non-image, non-citation element: images collected so far stay
            # pending — they may belong to an upcoming citation anchor.
            pass

    return groups
