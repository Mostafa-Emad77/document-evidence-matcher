"""
Shared citation text helpers.

These functions keep citation-only paragraph detection and trailing citation
extraction consistent across HTML, DOCX, quote linking, and segment matching.
"""
from __future__ import annotations

import re

YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}\b")

_STRONG_CITATION_MARKERS = (
    re.compile(r"\bpp?\.?\s*\d+", re.IGNORECASE),
    re.compile(r"\bpages?\s+\d+", re.IGNORECASE),
    re.compile(r"\bfile\s+no\.?", re.IGNORECASE),
    re.compile(r"\bsecurity\s+\d", re.IGNORECASE),
    re.compile(r"\bno\.?\s*\d+", re.IGNORECASE),
    re.compile(r"\bvol\.?\s*\d+", re.IGNORECASE),
    re.compile(r"\bchapter\s+\d+", re.IGNORECASE),
    re.compile(r"\bin\s*:", re.IGNORECASE),
    re.compile(r"\bdepartment\b", re.IGNORECASE),
    re.compile(r"\buniversity\b", re.IGNORECASE),
    re.compile(r"\bforeign\s+area\s+studies\b", re.IGNORECASE),
    re.compile(r"\bbulletin\b", re.IGNORECASE),
    re.compile(r"\bjournal\b", re.IGNORECASE),
    re.compile(r"\bpress\b", re.IGNORECASE),
    re.compile(r"\brecords?\s+office\b", re.IGNORECASE),
    re.compile(r"\bletter\s+no\.?", re.IGNORECASE),
    re.compile(r"\benclosure\b", re.IGNORECASE),
)


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _marker_count(text: str) -> int:
    return sum(1 for pattern in _STRONG_CITATION_MARKERS if pattern.search(text))


def is_citation_text(text: str, *, require_parentheses: bool = False) -> bool:
    """
    Return True for parenthetical source citations, including bibliography-style
    citations that have no explicit year but contain strong source markers.
    """
    normalized = normalize_text(text)
    if not normalized or len(normalized) > 2500:
        return False

    if normalized.startswith("(") and normalized.endswith(")"):
        inner = normalized[1:-1].strip()
    elif require_parentheses:
        return False
    else:
        inner = normalized

    if not inner:
        return False

    comma_count = inner.count(",")
    markers = _marker_count(inner)
    has_year = bool(YEAR_RE.search(inner))

    if has_year and (comma_count >= 2 or markers >= 1):
        return True

    # Bibliography-style citations can be valid without a visible year:
    # (Area Handbook..., Department..., University..., pp. 49-50)
    if comma_count >= 2 and markers >= 2:
        return True

    return False


def is_citation_only_text(text: str) -> bool:
    return is_citation_text(text, require_parentheses=True)


def extract_trailing_citation(text: str) -> str | None:
    """
    Extract the final balanced parenthetical citation from text.

    Handles nested parentheses such as:
    (Area Handbook for the United Arab Republic (Egypt), ..., pp. 49-50)
    """
    normalized = normalize_text(text)
    if not normalized.endswith(")"):
        return None

    depth = 0
    start = None
    for idx in range(len(normalized) - 1, -1, -1):
        ch = normalized[idx]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                start = idx
                break

    if start is None:
        return None

    candidate = normalized[start:].strip()
    if not is_citation_text(candidate, require_parentheses=True):
        return None
    return candidate
