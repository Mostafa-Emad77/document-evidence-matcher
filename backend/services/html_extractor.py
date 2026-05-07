"""
HTML extractor — parses Word-exported HTML files (.htm/.html).

Paragraph class mapping:
    MsoNormal  → narration
    Style1     → quote
    <img>      → image
    Citation   → citation-only paragraphs, not inline citations inside prose
"""
from __future__ import annotations

import base64
import re
import urllib.request
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, Tag

from backend.schemas import DocumentElement, ElementType, ExtractedImage
from backend.services.citation_text import extract_trailing_citation, is_citation_only_text

MERGE_SOFT_WRAPPED_NARRATION = True
_TERMINAL_PUNCT = (".", "!", "?", ":", "\"")
_SPEAKER_PREFIX_RE = re.compile(r"^\[(?:Q|A)\s*:\]", re.IGNORECASE)
NOISE_PATTERNS = (
    "back to home page",
    "sources:",
    "join sovinform",
    "mailing list",
    "follow sovinform",
    "twitter | telegram",
    "twitter|telegram",
    "email subject/title",
    "no further comment necessary",
    "sovinform.tech@gmail.com",
)


def _image_to_base64(src: str, base_dir: Path) -> Optional[str]:
    """Resolve image src relative to the HTML file and return base64 PNG."""
    try:
        if src.startswith("http://") or src.startswith("https://"):
            with urllib.request.urlopen(src, timeout=5) as resp:  # noqa: S310
                data = resp.read()
        else:
            img_path = (base_dir / src).resolve()
            if not img_path.is_file():
                return None
            data = img_path.read_bytes()
        return base64.b64encode(data).decode()
    except Exception:
        return None


def _read_html(path: Path) -> str:
    """
    Read HTML with correct encoding.
    Word exports often use windows-1252 despite claiming utf-8 in the meta tag.
    Strategy: read as latin-1 (lossless byte pass-through), let BeautifulSoup
    detect the declared charset from the <meta> tag, then re-read with that
    charset. Fall back to utf-8 → cp1252 → latin-1.
    """
    raw = path.read_bytes()
    # Quick scan for charset declaration
    sniff = raw[:4096].decode("latin-1", errors="replace").lower()
    declared = None
    for pattern in (r'charset=["\']?([\w-]+)', r'charset=([\w-]+)'):
        m = re.search(pattern, sniff)
        if m:
            declared = m.group(1).strip().lower()
            break

    for enc in filter(None, [declared, "utf-8-sig", "utf-8", "cp1252", "latin-1"]):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1")  # guaranteed lossless


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _is_noise_paragraph(text: str) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return True
    return any(
        normalized == pattern
        or normalized.startswith(pattern)
        or pattern in normalized
        for pattern in NOISE_PATTERNS
    )


def _ends_with_terminal_punctuation(text: str) -> bool:
    stripped = (text or "").rstrip()
    if not stripped:
        return False
    return stripped.endswith(_TERMINAL_PUNCT)


def _starts_speaker_turn(text: str) -> bool:
    return bool(_SPEAKER_PREFIX_RE.match((text or "").strip()))


def _is_heading_class(css_class: str) -> bool:
    class_tokens = [token.strip().lower() for token in css_class.split() if token.strip()]
    for token in class_tokens:
        if token in {"msotitle", "subtitle"}:
            return True
        if token.startswith("heading") or token.startswith("msoheading"):
            return True
    return False


def _css_length_to_cm(value: str) -> float | None:
    match = re.search(r"(-?\d+(?:\.\d+)?)(cm|pt|in)", value.strip().lower())
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "cm":
        return amount
    if unit == "pt":
        return amount / 28.3465
    if unit == "in":
        return amount * 2.54
    return None


def _style_property(style: str, name: str) -> str | None:
    for part in (style or "").split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        if key.strip().lower() == name:
            return value.strip()
    return None


def _is_indented_quote_style(style: str) -> bool:
    """
    Word-exported HTML sometimes stores quote blocks as MsoNormal paragraphs
    with matching left/right margins instead of a Quote/Style1 class.
    """
    left = _style_property(style, "margin-left")
    right = _style_property(style, "margin-right")
    if not left or not right:
        return False
    left_cm = _css_length_to_cm(left)
    right_cm = _css_length_to_cm(right)
    if left_cm is None or right_cm is None:
        return False
    return left_cm >= 0.5 and right_cm >= 0.5


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
    if not MERGE_SOFT_WRAPPED_NARRATION:
        return elements

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


def extract_from_html(html_path: str) -> list[DocumentElement]:
    """Parse a Word-exported HTML file into an ordered list of DocumentElements."""
    path = Path(html_path)
    soup = BeautifulSoup(_read_html(path), "html.parser")
    base_dir = path.parent

    elements: list[DocumentElement] = []
    position = 0

    def add(elem: DocumentElement) -> None:
        nonlocal position
        elem.position = position
        elements.append(elem)
        position += 1

    body = soup.find("body") or soup
    for node in body.descendants:
        if not isinstance(node, Tag):
            continue

        # ── Images ──────────────────────────────────────────────────────────
        if node.name == "img":
            src = node.get("src", "")
            img_id = f"img_{position}"
            thumbnail = _image_to_base64(src, base_dir)
            add(DocumentElement(
                position=0,
                type=ElementType.image,
                image=ExtractedImage(
                    image_id=img_id,
                    src=src,
                    position=position,
                    thumbnail_base64=thumbnail,
                ),
            ))
            continue

        # ── Paragraphs ───────────────────────────────────────────────────────
        if node.name != "p":
            continue

        # Skip empty paragraphs
        text = _normalize_text(node.get_text(separator=" "))
        if not text:
            continue
        if _is_noise_paragraph(text):
            continue

        css_class = " ".join(node.get("class", []))
        css_class_lower = css_class.lower()
        css_style = str(node.get("style", "") or "")

        # Only citation-only paragraphs become citation anchors.
        if is_citation_only_text(text):
            add(DocumentElement(position=0, type=ElementType.citation, text=text))
            continue

        # Fallback: many articles keep citations inline at the end of quote
        # paragraphs. Emit a citation anchor so downstream PDF matching has
        # a stable target even when no citation-only paragraph exists.
        trailing_citation = extract_trailing_citation(text)

        is_heading = _is_heading_class(css_class)

        if "style1" in css_class_lower or _is_indented_quote_style(css_style):
            add(DocumentElement(position=0, type=ElementType.quote, text=text))
        elif is_heading:
            add(DocumentElement(position=0, type=ElementType.heading, text=text))
        else:
            # MsoNormal and everything else → narration
            add(DocumentElement(position=0, type=ElementType.narration, text=text))

        if trailing_citation and not is_heading:
            add(DocumentElement(position=0, type=ElementType.citation, text=trailing_citation))

    return _merge_soft_wrapped_blocks(elements)
