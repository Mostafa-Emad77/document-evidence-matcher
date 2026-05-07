"""
Extract screenshot candidates from a PDF where each page is treated as one
visual evidence item.
"""
from __future__ import annotations

import base64
import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from openai import OpenAI

from backend.config import settings
from backend.schemas import ExtractedImage

_client = OpenAI(api_key=settings.openai_api_key)
_OCR_MODEL = "gpt-5.4-mini"
_OCR_DETAIL = "auto"
_OCR_MAX_WORKERS = 4
_OCR_MIN_TEXT_CHARS = 120
_OCR_MIN_TOKEN_COUNT = 24
_OCR_CACHE: dict[tuple[str, int], str] = {}
_OCR_CACHE_LOCK = threading.Lock()
_OCR_SYSTEM_PROMPT = (
    "You are an OCR engine. Transcribe all visible text from this page image in natural "
    "reading order. Include source names, titles, outlets, dates, and body text. "
    "Do not add commentary. Output plain text only."
)


@dataclass
class PDFPageEvidence:
    page_number: int
    image: ExtractedImage
    text: str


def _render_page_thumbnail_b64(page: fitz.Page) -> str:
    # Keep thumbnails compact for JSON + DOCX embedding.
    pix = page.get_pixmap(matrix=fitz.Matrix(0.55, 0.55), alpha=False)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def _render_page_ocr_b64(page: fitz.Page) -> str:
    # OCR needs higher resolution than the DOCX thumbnail.
    pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def _sha1_file(path: str) -> str:
    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _needs_ocr(page_text: str) -> bool:
    normalized = " ".join((page_text or "").split())
    if not normalized:
        return True
    # Word/PyMuPDF can return tiny fragments for scanned pages.
    if len(normalized) < _OCR_MIN_TEXT_CHARS:
        return True

    tokens = re.findall(r"[A-Za-z0-9]+", normalized)
    if len(tokens) < _OCR_MIN_TOKEN_COUNT:
        return True

    # Heuristic: low-quality extraction often has very low alphabetic density.
    alpha_chars = sum(1 for ch in normalized if ch.isalpha())
    alpha_density = alpha_chars / max(1, len(normalized))
    if alpha_density < 0.18:
        return True

    # Heuristic for mojibake/corrupted extraction.
    replacement_count = normalized.count("\uFFFD") + normalized.count("�")
    if replacement_count > 3:
        return True

    return False


def _ocr_page_with_vlm(png_b64: str) -> str:
    try:
        resp = _client.chat.completions.create(
            model=_OCR_MODEL,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _OCR_SYSTEM_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{png_b64}",
                                "detail": _OCR_DETAIL,
                            },
                        },
                    ],
                }
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _resolve_page_text(
    pdf_sha1: str,
    page_number: int,
    ocr_png_b64: str,
    fallback_text: str,
) -> str:
    cache_key = (pdf_sha1, page_number)
    with _OCR_CACHE_LOCK:
        cached = _OCR_CACHE.get(cache_key)
    if cached is not None:
        return cached

    ocr_text = _ocr_page_with_vlm(ocr_png_b64)
    resolved = (ocr_text or fallback_text or "").strip()
    with _OCR_CACHE_LOCK:
        _OCR_CACHE[cache_key] = resolved
    return resolved


def extract_pdf_page_evidence(pdf_path: str) -> list[PDFPageEvidence]:
    """
    Convert each PDF page into an evidence candidate with:
    - a rendered page thumbnail
    - extracted page text (for citation matching)
    """
    doc = fitz.open(pdf_path)
    file_name = Path(pdf_path).name

    page_rows: list[tuple[int, str, str, bool, str | None]] = []
    try:
        for idx, page in enumerate(doc):
            page_no = idx + 1
            page_text = (page.get_text("text") or "").strip()
            thumb_b64 = _render_page_thumbnail_b64(page)
            needs_ocr = _needs_ocr(page_text)
            ocr_b64 = _render_page_ocr_b64(page) if needs_ocr else None
            page_rows.append((page_no, page_text, thumb_b64, needs_ocr, ocr_b64))
    finally:
        doc.close()

    # Keep extracted text directly for normal text-rich pages.
    resolved_text_by_page: dict[int, str] = {
        page_no: page_text
        for page_no, page_text, _, needs_ocr, _ in page_rows
        if not needs_ocr
    }

    ocr_jobs = [
        (page_no, page_text, ocr_b64)
        for page_no, page_text, _, needs_ocr, ocr_b64 in page_rows
        if needs_ocr and ocr_b64
    ]
    if ocr_jobs:
        pdf_sha1 = _sha1_file(pdf_path)
        worker_count = max(1, min(_OCR_MAX_WORKERS, len(ocr_jobs)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_by_page = {
                page_no: executor.submit(_resolve_page_text, pdf_sha1, page_no, ocr_b64, page_text)
                for page_no, page_text, ocr_b64 in ocr_jobs
            }
            for page_no, future in future_by_page.items():
                resolved_text_by_page[page_no] = future.result()

    pages: list[PDFPageEvidence] = []
    for idx, (page_no, page_text, thumb_b64, _, _) in enumerate(page_rows):
        image = ExtractedImage(
            image_id=f"pdf_page_{page_no}",
            src=f"{file_name}#page={page_no}",
            position=idx,
            thumbnail_base64=thumb_b64,
        )
        pages.append(
            PDFPageEvidence(
                page_number=page_no,
                image=image,
                text=resolved_text_by_page.get(page_no, page_text),
            )
        )

    return pages
