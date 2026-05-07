"""
Assembler — orchestrates the full Milestone 1 pipeline and returns
the Milestone1Output model.
"""
from __future__ import annotations

from backend.schemas import Milestone1Output
from backend.services.citation_pdf_matcher import match_citations_to_pdf_pages
from backend.services.citation_grouper import build_citation_groups
from backend.services.docx_extractor import extract_from_docx
from backend.services.html_extractor import extract_from_html
from backend.services.pdf_screenshot_extractor import extract_pdf_page_evidence
from backend.services.quote_linker import extract_quote_links
from backend.services.semantic_matcher import match_segments_to_citations
from backend.services.span_colorizer import assign_claim_colors


def run_pipeline(
    html_path: str | None,
    screenshots_pdf_path: str | None = None,
    source_docx_path: str | None = None,
    span_coloring_level: str = "medium",
) -> Milestone1Output:
    """Run the full Milestone 1 pipeline on HTML or DOCX (+ optional screenshot PDF)."""

    # Phase 2: Parse document into ordered elements
    if source_docx_path:
        elements = extract_from_docx(source_docx_path)
    elif html_path:
        elements = extract_from_html(html_path)
    else:
        raise ValueError("Either html_path or source_docx_path must be provided.")

    # Phase 3: Group images under citation anchors
    citation_groups = build_citation_groups(elements)

    # Optional: replace/augment citation images using uploaded screenshot PDF.
    if screenshots_pdf_path:
        page_evidence = extract_pdf_page_evidence(screenshots_pdf_path)
        citation_to_pdf_image = match_citations_to_pdf_pages(citation_groups, page_evidence)
        for group in citation_groups:
            matched = citation_to_pdf_image.get(group.citation_id)
            if matched:
                # Prefer the uploaded screenshot PDF as the primary evidence image.
                group.images = [matched]

    # Phase 4: Semantic matching — each segment → best citation group
    segment_matches = match_segments_to_citations(elements, citation_groups)

    # Phase 5: Quote-to-description table + image links
    quote_links = extract_quote_links(elements, citation_groups)
    assign_claim_colors(quote_links, coloring_level=span_coloring_level)

    return Milestone1Output(
        segments=segment_matches,
        quote_table=quote_links,
        citation_groups=citation_groups,
    )
