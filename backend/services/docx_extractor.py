"""
DOCX extractor — parses Word files into ordered DocumentElements.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from docx import Document

from backend.schemas import DocumentElement, ElementType, ExtractedImage
from backend.services.citation_text import extract_trailing_citation, is_citation_only_text

_TERMINAL_PUNCT = (".", "!", "?", ":", "\"")
_SPEAKER_PREFIX_RE = re.compile(r"^\[(?:Q|A)\s*:\]", re.IGNORECASE)


def _is_quote_style(style_name: str) -> bool:
    return "quote" in (style_name or "").strip().lower()


def _is_heading_style(style_name: str) -> bool:
    normalized = (style_name or "").strip().lower()
    return (
        normalized.startswith("heading")
        or normalized == "title"
        or normalized == "subtitle"
    )


def _is_indented_quote_paragraph(paragraph) -> bool:
    fmt = paragraph.paragraph_format
    left = fmt.left_indent
    right = fmt.right_indent
    return (
        left is not None
        and right is not None
        and left.pt >= 10
        and right.pt >= 10
    )


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def _ends_with_terminal_punctuation(text: str) -> bool:
    stripped = (text or "").rstrip()
    if not stripped:
        return False
    return stripped.endswith(_TERMINAL_PUNCT)


def _starts_speaker_turn(text: str) -> bool:
    return bool(_SPEAKER_PREFIX_RE.match((text or "").strip()))


def _can_merge_soft_wrapped(prev: DocumentElement, cur: DocumentElement) -> bool:
    if prev.type != cur.type:
        return False
    if prev.type not in (ElementType.narration, ElementType.quote):
        return False
    if not prev.text or not cur.text:
        return False
    if _ends_with_terminal_punctuation(prev.text):
        return False
    if prev.type == ElementType.quote and _starts_speaker_turn(cur.text):
        return False
    return True


def _merge_soft_wrapped_blocks(elements: list[DocumentElement]) -> list[DocumentElement]:
    merged: list[DocumentElement] = []
    for elem in elements:
        if merged and _can_merge_soft_wrapped(merged[-1], elem):
            merged[-1].text = f"{merged[-1].text.rstrip()} {elem.text.lstrip()}"
            continue
        merged.append(elem.model_copy(deep=True))

    for idx, elem in enumerate(merged):
        elem.position = idx
        if elem.image is not None:
            elem.image.position = idx
    return merged


def _paragraph_images(paragraph, image_counter: int) -> tuple[list[ExtractedImage], int]:
    images: list[ExtractedImage] = []
    for run in paragraph.runs:
        rel_ids = run._r.xpath(".//a:blip/@r:embed")
        for rel_id in rel_ids:
            image_part = paragraph.part.related_parts.get(rel_id)
            if image_part is None:
                continue
            image_counter += 1
            image_bytes = image_part.blob
            images.append(
                ExtractedImage(
                    image_id=f"docx_img_{image_counter}",
                    src=f"docx://{rel_id}",
                    position=0,
                    thumbnail_base64=base64.b64encode(image_bytes).decode("ascii"),
                )
            )
    return images, image_counter


def extract_from_docx(docx_path: str) -> list[DocumentElement]:
    """Parse a DOCX file into an ordered list of DocumentElements."""
    path = Path(docx_path)
    doc = Document(str(path))

    elements: list[DocumentElement] = []
    position = 0
    image_counter = 0

    def add(elem: DocumentElement) -> None:
        nonlocal position
        elem.position = position
        if elem.image is not None:
            elem.image.position = position
        elements.append(elem)
        position += 1

    for paragraph in doc.paragraphs:
        para_images, image_counter = _paragraph_images(paragraph, image_counter)
        for image in para_images:
            add(DocumentElement(position=0, type=ElementType.image, image=image))

        text = _normalize_text(paragraph.text or "")
        if not text:
            continue
        if is_citation_only_text(text):
            add(DocumentElement(position=0, type=ElementType.citation, text=text))
            continue

        style_name = paragraph.style.name if paragraph.style else ""
        if _is_heading_style(style_name):
            para_type = ElementType.heading
        elif _is_quote_style(style_name) or _is_indented_quote_paragraph(paragraph):
            para_type = ElementType.quote
        else:
            para_type = ElementType.narration
        add(DocumentElement(position=0, type=para_type, text=text))

        trailing_citation = extract_trailing_citation(text)
        if trailing_citation and para_type != ElementType.heading:
            add(DocumentElement(position=0, type=ElementType.citation, text=trailing_citation))

    return _merge_soft_wrapped_blocks(elements)
