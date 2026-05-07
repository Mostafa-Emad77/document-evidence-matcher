from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Document element types
# ---------------------------------------------------------------------------

class ElementType(str, Enum):
    narration = "narration"
    quote = "quote"
    heading = "heading"
    image = "image"
    citation = "citation"


class ExtractedImage(BaseModel):
    image_id: str
    src: str                        # original src or internal reference
    position: int                   # sequential index in document
    thumbnail_base64: Optional[str] = None  # base64-encoded PNG thumbnail


class DocumentElement(BaseModel):
    position: int
    type: ElementType
    text: Optional[str] = None      # populated for narration / quote / citation
    image: Optional[ExtractedImage] = None  # populated for image elements


# ---------------------------------------------------------------------------
# Citation groups  (images grouped under a citation anchor)
# ---------------------------------------------------------------------------

class CitationGroup(BaseModel):
    citation_id: str                # e.g. "citation_4"
    citation_text: str              # full parenthetical string
    source_title: Optional[str] = None
    outlet: Optional[str] = None
    date: Optional[str] = None
    url: Optional[str] = None
    images: list[ExtractedImage] = []
    position: int                   # position of the citation element itself


# ---------------------------------------------------------------------------
# Quote <-> description linking
# ---------------------------------------------------------------------------

class QuoteImageLink(BaseModel):
    quote_id: str
    description_text: str
    quote_text: str
    citation_text: Optional[str] = None
    description_position: Optional[int] = None
    quote_start_position: Optional[int] = None
    quote_end_position: Optional[int] = None
    citation_position: Optional[int] = None
    nearest_image_id: Optional[str] = None
    image_position: Optional[int] = None
    claim_spans: list["ClaimSpan"] = []
    # When the visible description is too short to host meaningful highlights,
    # the colorizer computes claim spans against a borrowed (previous) description
    # instead. The visible description_text and description_position are kept as-is
    # so the "Go to description" hyperlink still points to the original intro.
    colorization_description_text: Optional[str] = None
    colorization_description_position: Optional[int] = None


class ClaimSpan(BaseModel):
    description_start: int
    description_end: int
    quote_start: int
    quote_end: int
    color: str
    group_id: str = ""
    description_unit: str = ""
    quote_span_text: str = ""


# ---------------------------------------------------------------------------
# Semantic matching output
# ---------------------------------------------------------------------------

class SegmentMatch(BaseModel):
    segment_id: str
    segment_text: str
    segment_type: ElementType
    matched_citation_id: Optional[str] = None
    matched_citation_text: Optional[str] = None
    match_score: Optional[float] = None
    matched_images: list[ExtractedImage] = []


# ---------------------------------------------------------------------------
# Full Milestone 1 output
# ---------------------------------------------------------------------------

class Milestone1Output(BaseModel):
    segments: list[SegmentMatch]
    quote_table: list[QuoteImageLink]
    citation_groups: list[CitationGroup]
    # Wall time for run_pipeline + generate_docx (seconds); set by API layer.
    parse_duration_seconds: Optional[float] = None
    # Cache tracking — helps debug stale vs fresh results
    generated_at: Optional[str] = None  # ISO 8601 timestamp of actual generation
    from_cache: bool = False           # True if served from cached result
    cache_key: Optional[str] = None    # Identifier for cache entry (if applicable)
