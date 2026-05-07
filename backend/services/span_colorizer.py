"""
LLM-assisted span pairing between description and quote text.

For each QuoteImageLink, produces non-overlapping description/quote span pairs
and assigns a shared highlight color per pair.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import math
import re
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    AsyncOpenAI,
    AuthenticationError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

logger = logging.getLogger("autocon.span_colorizer")

_FATAL_OPENAI_ERRORS = (
    RateLimitError,
    AuthenticationError,
    PermissionDeniedError,
    APIConnectionError,
)

from backend.config import settings
from backend.schemas import ClaimSpan, QuoteImageLink

_EXTRACT_MODEL = "gpt-5.4-mini"
_VERIFY_MODEL = "gpt-5.4-mini"
_EMBED_MODEL = "text-embedding-3-large"
_EXTRACT_REASONING_EFFORT = "medium"
_VERIFY_REASONING_EFFORT = "low"
_TOP_K_PER_UNIT = 10
_MIN_COSINE = 0.42
_MIN_SCORE = 0.22
_LENGTH_PENALTY = 0.02
_MAX_CANDIDATES = 280
_VERY_SMALL_QUOTE_MAX_LINES = 2
# Very short quotes (1–2 lines) stay conservative so we do not over-paint tiny blocks.
_VERY_SMALL_QUOTE_MAX_MATCHES_PER_UNIT = 3
_SMALL_QUOTE_TOKEN_LIMIT = 90
# Ratio cap: one description highlight may point to multiple quote highlights,
# but not unlimited repeats. Smaller quotes allow about 1:7; larger quotes 1:12.
_SMALL_QUOTE_MAX_MATCHES_PER_UNIT = 7
_LARGE_QUOTE_MAX_MATCHES_PER_UNIT = 12
_MAX_TOTAL_MATCHES_PER_ROW = 18
_MAX_CONCURRENCY = 16
_KIND_MIN_COSINE = {
    "entity": 0.48,
    "action": 0.44,
    "relation": 0.42,
}
_KIND_MIN_SCORE = {
    "entity": 0.33,
    "action": 0.26,
    "relation": 0.21,
}
_KIND_OVERLAP_WEIGHT = {
    "entity": 0.30,
    "action": 0.16,
    "relation": 0.10,
}
_KIND_MIN_TOKEN_COVERAGE = {
    "entity": 0.40,
    "action": 0.55,
    "relation": 0.55,
}
# Relaxed thresholds used only as a last-chance fallback when the strict pass
# yields zero accepted proposals for a row. Keeps at least one visual link
# between a substantial description and its quote.
_FALLBACK_MIN_COSINE = 0.38
_FALLBACK_MIN_SCORE = 0.15
_FALLBACK_MIN_TOKEN_COVERAGE = 0.28
_FALLBACK_MAX_PROPOSALS_PER_UNIT = 3
_FALLBACK_MAX_TOTAL_ACCEPTED = 6
_FALLBACK_MIN_DESC_TOKENS = 5
_MIN_LENIENT_SPAN_SCORE = 0.52
_DENSE_TOP_K_PER_UNIT = 1
_DENSE_MAX_REPEATS_PER_UNIT = 3
_DENSE_MAX_TOTAL_SPANS = 14
_DENSE_MAX_UNITS = 16
_DENSE_RELAXED_MIN_COVERAGE = 0.22
_DENSE_RELAXED_MIN_SCORE = 0.14
_MAX_MIRRORS_PER_GROUP = 7
_WEAK_BOUNDARY_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "because",
    "by",
    "calling",
    "could",
    "despite",
    "final",
    "first",
    "for",
    "from",
    "he",
    "her",
    "his",
    "however",
    "in",
    "including",
    "into",
    "is",
    "it",
    "its",
    "last",
    "moreover",
    "of",
    "on",
    "or",
    "our",
    "points",
    "second",
    "she",
    "since",
    "that",
    "the",
    "their",
    "these",
    "third",
    "those",
    "this",
    "to",
    "try",
    "unfortunately",
    "was",
    "were",
    "who",
    "which",
    "with",
    "would",
}
# Filler phrases to aggressively strip from span beginnings
_FILLER_START_PATTERNS = [
    re.compile(r"^(?:a\s+few\s+final\s+points[:;]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:final\s+points[:;]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:first(?:ly)?[:;,]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:second(?:ly)?[:;,]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:third(?:ly)?[:;,]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:lastly[:;,]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:moreover[:;,]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:however[:;,]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:unfortunately[:;,]?\s*)", re.IGNORECASE),
    re.compile(r"^(?:indeed[:;,]?\s*)", re.IGNORECASE),
]
_QUOTED_UNIT_RE = re.compile(
    r"(?:[\"“](?P<double>[^\"”]{2,120})[\"”])"
    r"|(?:‘(?P<single>[^’]{2,120})’)"
    r"|(?:(?<!\w)'(?P<straight>[^']{2,120})'(?!\w))"
    r"|(?:�(?P<mojibake>[^�]{2,120})�)"
)
# WD_COLOR_INDEX names for docx_writer._enum_color.
# Order: pastel / light fills first (best contrast with black text), then Word’s
# standard bright RED and BLUE highlights (still readable; avoid DARK_* and
# GRAY_50, TEAL, VIOLET, GREEN, BLACK which read as muddy or heavy).
# WHITE is excluded because it renders as no visible highlight on a white page.
# Each row may use at most len(_HIGHLIGHT_PALETTE) units to keep colors unique.
_HIGHLIGHT_PALETTE = [
    "YELLOW",
    "TURQUOISE",
    "BRIGHT_GREEN",
    "PINK",
    "GRAY_25",
    "BLUE",
    "RED",
]
_MAX_UNITS = len(_HIGHLIGHT_PALETTE)


def _unit_color(unit_index: int) -> str:
    return _HIGHLIGHT_PALETTE[unit_index % len(_HIGHLIGHT_PALETTE)]


def _max_matches_per_unit(quote_text: str) -> int:
    non_empty_lines = [line for line in (quote_text or "").splitlines() if line.strip()]
    if 0 < len(non_empty_lines) <= _VERY_SMALL_QUOTE_MAX_LINES:
        return _VERY_SMALL_QUOTE_MAX_MATCHES_PER_UNIT

    token_count = len(_TOKEN_RE.findall(quote_text or ""))
    if token_count <= _SMALL_QUOTE_TOKEN_LIMIT:
        return _SMALL_QUOTE_MAX_MATCHES_PER_UNIT
    return _LARGE_QUOTE_MAX_MATCHES_PER_UNIT


def _exact_reference_bonus(unit_text: str, candidate_text: str) -> float:
    unit_norm = " ".join((unit_text or "").lower().split())
    cand_norm = " ".join((candidate_text or "").lower().split())
    if not unit_norm or not cand_norm:
        return 0.0
    if cand_norm == unit_norm:
        return 0.12
    if re.search(rf"\b{re.escape(unit_norm)}\b", cand_norm):
        return 0.06
    return 0.0
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_STOP_TOKENS = {
    "the",
    "and",
    "for",
    "from",
    "with",
    "that",
    "this",
    "these",
    "those",
    "was",
    "were",
    "are",
    "is",
    "has",
    "have",
    "had",
    "into",
    "onto",
    "over",
    "under",
    "than",
    "then",
    "after",
    "before",
    "while",
    "during",
    "about",
    "around",
    "against",
    "between",
    "within",
    "party",
    "group",
    "organization",
    "president",
    "leader",
    "commander",
    "minister",
    "director",
    "vice",
    "supreme",
}


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """
    Return whitespace-normalized text and a character index map.

    The returned map stores, for each normalized character position, the
    original character index in `text`.
    """
    out_chars: list[str] = []
    out_map: list[int] = []
    prev_space = False

    for idx, ch in enumerate(text):
        if ch.isspace():
            if not out_chars or prev_space:
                continue
            out_chars.append(" ")
            out_map.append(idx)
            prev_space = True
            continue
        out_chars.append(ch)
        out_map.append(idx)
        prev_space = False

    if out_chars and out_chars[-1] == " ":
        out_chars.pop()
        out_map.pop()
    return "".join(out_chars), out_map


def _parse_json_object(content: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def _quoted_description_units(description_text: str) -> list[dict[str, str]]:
    """
    Deterministically promote short quoted claim phrases from the description.

    These are often the author's exact claim anchors (for example,
    "on fighting the Soviets", 'a great deal', or ‘Al-Hizb el-Watani’) and
    should be highlighted before broader entity matches selected by the LLM.
    """
    units: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _QUOTED_UNIT_RE.finditer(description_text or ""):
        raw = next((group for group in match.groups() if group), "")
        text = " ".join(raw.split()).strip(".,;:!?()[]{}")
        if not text:
            continue
        word_count = len(_TOKEN_RE.findall(text))
        if word_count < 2 or word_count > 7:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        units.append({"text": text, "kind": "relation"})
        if len(units) >= _MAX_UNITS:
            break
    return units


def _shared_description_units(description_text: str, quote_text: str) -> list[dict[str, str]]:
    """
    Promote exact shared phrases from the description into deterministic units.

    This fills gaps where the LLM skips obvious anchors like "restoration of
    order" even though that exact phrase appears in the quote.
    """
    desc_norm = " ".join((description_text or "").casefold().split())
    quote_norm = " ".join((quote_text or "").casefold().split())
    if not desc_norm or not quote_norm:
        return []

    word_matches = list(re.finditer(r"\b[A-Za-z][A-Za-z'-]*\b", description_text or ""))
    candidates: list[tuple[int, int, int, str]] = []
    seen: set[str] = set()
    for win in range(5, 1, -1):
        for i in range(0, max(0, len(word_matches) - win + 1)):
            start = word_matches[i].start()
            end = word_matches[i + win - 1].end()
            phrase = description_text[start:end].strip()
            phrase_norm = " ".join(phrase.casefold().split())
            if phrase_norm in seen:
                continue
            content_tokens = _tokenize(phrase, drop_stopwords=True)
            if len(content_tokens) < 2:
                continue
            if re.search(rf"\b{re.escape(phrase_norm)}\b", quote_norm) is None:
                continue
            seen.add(phrase_norm)
            candidates.append((len(content_tokens), start, end, phrase))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    units: list[dict[str, str]] = []
    used_ranges: list[tuple[int, int]] = []
    for _, start, end, phrase in candidates:
        if _has_overlap(used_ranges, start, end):
            continue
        units.append({"text": phrase, "kind": "relation"})
        used_ranges.append((start, end))
        if len(units) >= _MAX_UNITS:
            break
    return units


def _overlap_description_units(description_text: str, quote_text: str) -> list[dict[str, str]]:
    """
    Add obvious description-side anchors that fuzzily appear in the quote.

    The LLM can skip short entities/events such as "Egypt", "Wafd Party", or
    "Great War". This pass proposes compact capitalized phrases and meaningful
    content windows when they have token-level overlap with the quote.
    """
    quote_tokens = _tokenize(quote_text, drop_stopwords=True)
    if not quote_tokens:
        return []

    candidates: list[tuple[float, int, str, str]] = []
    seen: set[str] = set()

    def add_candidate(text: str, start: int, kind: str) -> None:
        cleaned = " ".join((text or "").split()).strip(".,;:!?()[]{}")
        if not cleaned:
            return
        key = cleaned.casefold()
        if key in seen:
            return
        tokens = _tokenize(cleaned, drop_stopwords=True)
        if not tokens:
            return
        if len(tokens) == 1 and len(next(iter(tokens))) < 5:
            return
        overlap = _fuzzy_intersection_count(tokens, quote_tokens)
        if overlap <= 0:
            return
        coverage = overlap / len(tokens)
        # Single named entities are useful, multi-token phrases need enough
        # support to avoid painting weak generic fragments.
        if len(tokens) == 1:
            if coverage < 1.0:
                return
        elif coverage < 0.50:
            return
        seen.add(key)
        score = coverage + min(len(tokens), 4) * 0.05
        candidates.append((score, start, cleaned, kind))

    cap_re = re.compile(
        r"\b(?:US|USSR|CIA|MI6|[A-Z][A-Za-z'’-]+)"
        r"(?:\s+(?:of|the|and|for|in|to|US|USSR|CIA|MI6|[A-Z][A-Za-z'’-]+)){0,5}"
    )
    for match in cap_re.finditer(description_text or ""):
        add_candidate(match.group(0), match.start(), "entity")

    # Lowercase rolling windows are intentionally not used here: they produce
    # awkward fragments ("in Egypt emerged by"). Exact lowercase overlaps are
    # already handled by _shared_description_units().

    candidates.sort(key=lambda item: (-item[0], item[1]))
    units: list[dict[str, str]] = []
    used_ranges: list[tuple[int, int]] = []
    desc_norm, desc_map = _normalize_with_map(description_text)
    for _, _, phrase, kind in candidates:
        occurrences = _find_span_candidates(desc_norm, desc_map, phrase)
        if not occurrences:
            continue
        start, end, _, _ = occurrences[0]
        if _has_overlap(used_ranges, start, end):
            continue
        units.append({"text": phrase, "kind": kind})
        used_ranges.append((start, end))
        if len(units) >= _MAX_UNITS:
            break
    return units


def _find_span_candidates(
    normalized_text: str,
    idx_map: list[int],
    candidate: str,
) -> list[tuple[int, int, int, int]]:
    normalized_candidate = " ".join((candidate or "").split()).strip()
    if not normalized_candidate:
        return []

    out: list[tuple[int, int, int, int]] = []
    search_from = 0
    while True:
        start_norm = normalized_text.find(normalized_candidate, search_from)
        if start_norm < 0:
            break
        end_norm = start_norm + len(normalized_candidate)
        start_orig = idx_map[start_norm]
        end_orig = idx_map[end_norm - 1] + 1
        out.append((start_orig, end_orig, start_norm, end_norm))
        search_from = start_norm + 1
    return out


def _find_best_span_lenient(text: str, candidate: str) -> tuple[int, int] | None:
    """
    Resolve an LLM phrase to a best-effort span in text.

    Uses exact normalized lookup first; if not found, picks the best token-window
    overlap match so semantic phrases still map to spans.
    """
    norm_text, idx_map = _normalize_with_map(text)
    exact = _find_span_candidates(norm_text, idx_map, candidate)
    if exact:
        start, end, _, _ = exact[0]
        return start, end

    cand_tokens_list = [m.group(0) for m in re.finditer(r"\b[A-Za-z][A-Za-z'-]*\b", candidate or "")]
    if not cand_tokens_list:
        return None
    cand_tokens = {t.lower().strip("'-").replace("-", "") for t in cand_tokens_list}
    cand_tokens = {t for t in cand_tokens if len(t) >= 3}
    if not cand_tokens:
        return None

    word_matches = list(re.finditer(r"\b[A-Za-z][A-Za-z'-]*\b", text or ""))
    if not word_matches:
        return None

    target_len = max(1, len(cand_tokens_list))
    best: tuple[float, int, int] | None = None
    min_win = max(1, target_len - 2)
    max_win = min(len(word_matches), target_len + 3)
    for win in range(min_win, max_win + 1):
        for i in range(0, len(word_matches) - win + 1):
            j = i + win - 1
            start = word_matches[i].start()
            end = word_matches[j].end()
            window_text = text[start:end]
            win_tokens = _tokenize(window_text, drop_stopwords=False)
            if not win_tokens:
                continue
            overlap = _fuzzy_intersection_count(cand_tokens, win_tokens)
            coverage = overlap / max(1, len(cand_tokens))
            score = coverage - (abs(win - target_len) * 0.02)
            candidate_best = (score, start, end)
            if best is None or candidate_best[0] > best[0]:
                best = candidate_best

    if best is None:
        return None
    if best[0] < _MIN_LENIENT_SPAN_SCORE:
        return None
    return best[1], best[2]


def _find_spans_for_phrase(text: str, phrase: str, max_matches: int) -> list[tuple[int, int]]:
    if max_matches <= 0:
        return []

    norm_text, idx_map = _normalize_with_map(text)
    exact = _find_span_candidates(norm_text, idx_map, phrase)
    if exact:
        out: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for start, end, _, _ in exact:
            key = (start, end)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
            if len(out) >= max_matches:
                break
        return out

    best = _find_best_span_lenient(text, phrase)
    if best is None:
        return []
    return [best]


def _find_exact_repeat_spans(text: str, phrase: str, max_matches: int) -> list[tuple[int, int]]:
    if max_matches <= 0:
        return []
    norm_text, idx_map = _normalize_with_map(text)
    exact = _find_span_candidates(norm_text, idx_map, phrase)
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for start, end, _, _ in exact:
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= max_matches:
            break
    return out


def _expand_quote_span_for_group(
    quote_text: str,
    start: int,
    end: int,
    description_span_text: str,
) -> tuple[int, int]:
    words = list(re.finditer(r"\b[A-Za-z][A-Za-z'-]*\b", quote_text or ""))
    if not words:
        return start, end

    hit_idx = [idx for idx, match in enumerate(words) if not (match.end() <= start or match.start() >= end)]
    if not hit_idx:
        return start, end

    first_idx = min(hit_idx)
    last_idx = max(hit_idx)

    original_text = quote_text[start:end]
    original_coverage = _token_coverage(description_span_text, original_text)
    original_overlap = _lexical_overlap(description_span_text, original_text)

    best = (original_coverage, original_overlap, start, end)

    # Try nearby windows around the hit to recover fuller phrase mirrors.
    for left in range(0, 3):
        for right in range(0, 5):
            i = max(0, first_idx - left)
            j = min(len(words) - 1, last_idx + right)
            c_start = words[i].start()
            c_end = words[j].end()
            candidate_text = quote_text[c_start:c_end]
            token_count = len(_TOKEN_RE.findall(candidate_text))
            if token_count < 3 or token_count > 16:
                continue
            coverage = _token_coverage(description_span_text, candidate_text)
            overlap = _lexical_overlap(description_span_text, candidate_text)
            candidate = (coverage, overlap, c_start, c_end)
            if candidate[:2] > best[:2]:
                best = candidate

    # Avoid over-expanding on tiny gains.
    if best[0] >= original_coverage + 0.08 or best[1] >= original_overlap + 0.06:
        return best[2], best[3]
    return start, end


def _group_aliases(description_span_text: str, quote_phrases: list[Any]) -> list[str]:
    texts = [description_span_text, *(str(phrase) for phrase in quote_phrases)]
    aliases: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in re.finditer(
            r"\b(?:[A-Z]{2,8}|[A-Z][A-Za-z'’-]+)"
            r"(?:\s+(?:of|the|and|for|in|to|on|at|[A-Z]{2,8}|[A-Z][A-Za-z'’-]+)){0,5}",
            text or "",
        ):
            alias = " ".join(match.group(0).split()).strip(".,;:!?()[]{}")
            if len(alias) < 4:
                continue
            key = alias.casefold()
            if key in seen:
                continue
            seen.add(key)
            aliases.append(alias)

        for token in _TOKEN_RE.findall(text or ""):
            cleaned = token.strip("'-")
            if len(cleaned) < 4:
                continue
            if cleaned.casefold() in _STOP_TOKENS:
                continue
            if not (cleaned[:1].isupper() or cleaned.isupper()):
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            aliases.append(cleaned)
    return aliases


def _alias_mirror_spans(
    quote_text: str,
    aliases: list[str],
    max_matches: int,
    used_quote_exact: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    if max_matches <= 0:
        return []
    words = list(re.finditer(r"\b[A-Za-z][A-Za-z'’-]*\b", quote_text or ""))
    if not words:
        return []
    spans: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for alias in aliases:
        pattern = re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
        for match in pattern.finditer(quote_text or ""):
            hit_idx = [
                idx
                for idx, word in enumerate(words)
                if not (word.end() <= match.start() or word.start() >= match.end())
            ]
            if not hit_idx:
                continue
            first_idx = min(hit_idx)
            last_idx = max(hit_idx)
            i = max(0, first_idx - 2)
            j = min(len(words) - 1, last_idx + 6)
            start = words[i].start()
            end = words[j].end()
            key = (start, end)
            if key in seen or key in used_quote_exact:
                continue
            if len(_TOKEN_RE.findall(quote_text[start:end])) < 3:
                continue
            seen.add(key)
            spans.append(key)
            if len(spans) >= max_matches:
                return spans
    return spans


def _split_description_span_candidates(description_span: str) -> list[str]:
    """
    Split a broad description span into smaller concept-like chunks.

    This helps avoid collapsing many quote links into only one group when the
    LLM returns a long multi-sentence description span.
    """
    base = " ".join((description_span or "").split()).strip()
    if not base:
        return []

    # If the span is already compact, keep it as-is.
    if len(_TOKEN_RE.findall(base)) <= 18:
        return [base]

    chunks: list[str] = []
    seen: set[str] = set()

    # Sentence-ish split first.
    sentence_parts = [
        p.strip(" ,;:-")
        for p in re.split(r"(?<=[.!?;:])\s+", base)
        if p and p.strip(" ,;:-")
    ]
    if not sentence_parts:
        sentence_parts = [base]

    for sentence in sentence_parts:
        # Further split long sentences by commas / dashes.
        sub_parts = [
            s.strip(" ,;:-")
            for s in re.split(r"\s*[,—-]\s*|\s*,\s*", sentence)
            if s and s.strip(" ,;:-")
        ]
        if not sub_parts:
            sub_parts = [sentence]

        for part in sub_parts:
            token_count = len(_TOKEN_RE.findall(part))
            if token_count < 3:
                continue
            # Keep moderately sized concept spans.
            if token_count > 24:
                continue
            key = part.casefold()
            if key in seen:
                continue
            seen.add(key)
            chunks.append(part)

    if not chunks:
        return [base]
    return chunks


def _has_overlap(intervals: list[tuple[int, int]], start: int, end: int) -> bool:
    for cur_start, cur_end in intervals:
        if not (end <= cur_start or start >= cur_end):
            return True
    return False


def _is_valid_description_for_mapping(description: str) -> bool:
    text = (description or "").strip()
    if not text:
        return False
    if _is_weak_description(text):
        return False
    if len(_TOKEN_RE.findall(text)) < _FALLBACK_MIN_DESC_TOKENS:
        return False
    return _has_substantive_content(text)


async def _extract_units(
    client: AsyncOpenAI, description_text: str
) -> list[dict[str, str]]:
    prompt = (
        "Extract semantic units from DESCRIPTION.\n"
        "Return JSON only with this exact shape:\n"
        "{\"units\":[{\"text\":\"...\",\"kind\":\"entity|action|relation\"}]}\n\n"
        "Rules:\n"
        f"- Maximum {_MAX_UNITS} units.\n"
        "- Each unit text MUST be an exact contiguous substring of DESCRIPTION.\n"
        "- Unit size MUST be at least 3 words. Single-word units are forbidden\n"
        "  (they produce trivial word-repetition matches, not semantic links).\n"
        "- Maximum 6 words per unit.\n"
        "- Units must be non-overlapping and ordered by appearance.\n"
        "- PRECISION OVER COVERAGE: it is far better to return 2 strong units than\n"
        "  6 weak ones. Every unit must represent a specific, substantive concept\n"
        "  that has a DIRECT semantic equivalent or very strong synonym in QUOTE.\n"
        "- If you are not confident that a unit has a clear counterpart in QUOTE,\n"
        "  omit it.\n"
        "- Prefer multi-word named entities and specific phrases:\n"
        "  'national liberation movement', 'colonial administrative control',\n"
        "  'state security council', 'foreign policy directive'.\n"
        "- NEVER extract bare nationality/adjective words as standalone units\n"
        "  (e.g. 'Egyptian', 'Soviet', 'British'). These always match by mere\n"
        "  word repetition and add no semantic value.\n"
        "- NEVER extract a book or document title as a unit unless the SAME title\n"
        "  also appears verbatim in QUOTE.\n"
        "- Split broad ideas into separate compact units instead of one broad unit.\n"
        "- Avoid generic source words by themselves: report, document, stated,\n"
        "  quoted, following, evidence, situation.\n"
        "- Never create an anchor from a broad category term when only a narrower\n"
        "  sub-part appears in QUOTE (for example linking 'Organization' to\n"
        "  'Afghan').\n"
        "- The description side highlights must remain visually distinct from each\n"
        "  other, so do not choose overlapping units.\n"
        "- If no suitable units exist, return {\"units\":[]}.\n\n"
        f"DESCRIPTION:\n{description_text}\n\n"
    )

    response = await client.chat.completions.create(
        model=_EXTRACT_MODEL,
        reasoning_effort=_EXTRACT_REASONING_EFFORT,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    content = (response.choices[0].message.content or "").strip()
    data = _parse_json_object(content)
    units = data.get("units")
    if not isinstance(units, list):
        return []
    result: list[dict[str, str]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        text = str(unit.get("text", "")).strip()
        kind = str(unit.get("kind", "")).strip().lower()
        if not text:
            continue
        if kind not in {"entity", "action", "relation"}:
            kind = "entity"
        if len(text.split()) > 6:
            continue
        result.append({"text": text, "kind": kind})
        if len(result) >= _MAX_UNITS:
            break
    return result


def _quote_candidates(quote_text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    clause_re = re.finditer(r"[^.;,]+", quote_text)
    for clause_match in clause_re:
        clause = clause_match.group(0)
        clause_start = clause_match.start()
        words = list(re.finditer(r"\b[\w'-]+\b", clause))
        if len(words) < 2:
            continue

        for i in range(len(words)):
            for win in range(2, 8):
                j = i + win - 1
                if j >= len(words):
                    break
                start = clause_start + words[i].start()
                end = clause_start + words[j].end()
                key = (start, end)
                if key in seen:
                    continue
                seen.add(key)
                text = quote_text[start:end]
                candidates.append(
                    {
                        "start": start,
                        "end": end,
                        "text": text,
                        "token_count": win,
                    }
                )
                if len(candidates) >= _MAX_CANDIDATES:
                    return candidates
    return candidates


def _embed_batch(client: OpenAI, texts: list[str], model: str) -> list[list[float]]:
    if not texts:
        return []
    response = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return -1.0
    return dot / (mag_a * mag_b)


def _tokenize(text: str, *, drop_stopwords: bool) -> set[str]:
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall((text or "").lower()):
        token = raw.strip("'-").replace("-", "")
        if len(token) < 3:
            continue
        if drop_stopwords and token in _STOP_TOKENS:
            continue
        tokens.add(token)
    return tokens


def _has_fuzzy_overlap(a_tokens: set[str], b_tokens: set[str]) -> bool:
    for a in a_tokens:
        for b in b_tokens:
            if a == b:
                return True
            if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
                return True
    return False


def _fuzzy_intersection_count(a_tokens: set[str], b_tokens: set[str]) -> int:
    matched = 0
    used_b: set[str] = set()
    for a in a_tokens:
        for b in b_tokens:
            if b in used_b:
                continue
            if a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a)):
                matched += 1
                used_b.add(b)
                break
    return matched


def _token_coverage(unit_text: str, candidate_text: str) -> float:
    unit_tokens = _tokenize(unit_text, drop_stopwords=True)
    cand_tokens = _tokenize(candidate_text, drop_stopwords=True)
    if not unit_tokens or not cand_tokens:
        return 0.0
    return _fuzzy_intersection_count(unit_tokens, cand_tokens) / len(unit_tokens)


def _entity_gate(unit_text: str, candidate_text: str) -> bool:
    unit_tokens = _tokenize(unit_text, drop_stopwords=True)
    cand_tokens = _tokenize(candidate_text, drop_stopwords=True)
    if not unit_tokens or not cand_tokens:
        unit_tokens = _tokenize(unit_text, drop_stopwords=False)
        cand_tokens = _tokenize(candidate_text, drop_stopwords=False)
    if not unit_tokens or not cand_tokens:
        return False
    return _has_fuzzy_overlap(unit_tokens, cand_tokens)


def _pair_passes_gate(kind: str, unit_text: str, candidate_text: str) -> bool:
    coverage = _token_coverage(unit_text, candidate_text)
    min_coverage = _KIND_MIN_TOKEN_COVERAGE.get(kind, 0.67)
    if coverage >= min_coverage:
        return True

    # Keep named-entity links a little more tolerant for spelling variants.
    if kind == "entity" and _entity_gate(unit_text, candidate_text):
        return coverage >= 0.40

    return False


def _lexical_overlap(unit_text: str, candidate_text: str) -> float:
    unit_tokens = _tokenize(unit_text, drop_stopwords=True)
    cand_tokens = _tokenize(candidate_text, drop_stopwords=True)
    if not unit_tokens or not cand_tokens:
        return 0.0
    return len(unit_tokens & cand_tokens) / len(unit_tokens | cand_tokens)


def _is_shell_phrase(text: str) -> bool:
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return True
    shell_patterns = (
        r"\baccording to\b",
        r"\b(as follows|the following)\b",
        r"\bin the words of\b",
        r"\b(quoted|cited|reported|stated)\s+(in|by)\b",
        r"\bwas\s+quoted\s+by\b",
        r"\b(it|this|that)\s+(is|was|were|has been)\s+(stated|reported|noted|claimed|described)\b",
        r"\b(the\s+)?(report|document|source|statement)\s+(states|stated|reports|reported|notes|noted|claims|claimed)\b",
        r"\b(press|newspaper|bulletin|journal|magazine)\s+(reports?|reported|stated|states|claimed|claims)\b",
        r"\b(reports?|reported|stated|states|claimed|claims)\s+that\b",
        r"\b(further\s+)?evidence\s+has\s+(accumulated|been\s+found)\b",
        r"\bthe situation\s+(is|was|has been)\b",
    )
    return any(re.search(pattern, normalized) is not None for pattern in shell_patterns)


def _related_evidence_gate(description_span_text: str, quote_span_text: str) -> bool:
    if _is_shell_phrase(description_span_text) or _is_shell_phrase(quote_span_text):
        return False
    desc_tokens = _tokenize(description_span_text, drop_stopwords=True)
    quote_tokens = _tokenize(quote_span_text, drop_stopwords=True)
    if not desc_tokens or not quote_tokens:
        return False
    if _fuzzy_intersection_count(desc_tokens, quote_tokens) >= 1:
        return True
    return False


def _is_specialized_token(token: str) -> bool:
    t = (token or "").strip("'-").lower()
    if len(t) < 5:
        return False
    return t.endswith(("ism", "ist", "ists", "ization", "isation", "ology", "ocracy", "cracy"))


def _span_word_count_ok(text: str) -> bool:
    words = _TOKEN_RE.findall(text or "")
    if len(words) >= 3:
        return True
    if len(words) >= 2 and any(_is_specialized_token(word) or len(word) >= 8 for word in words):
        return True
    return False


def _has_substantive_content(text: str) -> bool:
    words = _TOKEN_RE.findall(text or "")
    if not words:
        return False
    if _is_shell_phrase(text):
        return False
    content = [w for w in words if w.lower() not in _STOP_TOKENS]
    ratio = len(content) / len(words)
    return ratio >= 0.45


_WEAK_DESC_MIN_WORDS = 8


def _is_weak_description(description: str) -> bool:
    """
    Detect if a description is too weak to produce meaningful highlights.
    
    Weak if:
    - Too short (< 8 words)
    - Lacks identifiable entities/topics (no capitalized terms, no content tokens)
    - Not substantive content
    """
    text = (description or "").strip()
    if not text:
        return True
    
    words = _TOKEN_RE.findall(text)
    if len(words) < _WEAK_DESC_MIN_WORDS:
        return True
    
    if not _has_substantive_content(text):
        return True
    
    # Check for identifiable entities (capitalized words)
    has_entity = any(
        word[0].isupper() and len(word) >= 3
        for word in words
        if word and word[0].isalpha()
    )
    if not has_entity:
        return True
    
    return False


def _span_quality(description_unit: str, quote_span_text: str) -> float:
    return (_token_coverage(description_unit, quote_span_text) * 0.7) + (
        _lexical_overlap(description_unit, quote_span_text) * 0.3
    )


def _boundary_quality_bonus(text: str) -> float:
    words = _TOKEN_RE.findall(text or "")
    if not words:
        return -1.0
    first = words[0].casefold()
    last = words[-1].casefold()
    score = 0.0
    if first in _WEAK_BOUNDARY_WORDS:
        score -= 0.35
    if last in _WEAK_BOUNDARY_WORDS:
        score -= 0.45
    if (text or "").strip().endswith((".", "!", "?", "”", "\"", "'")):
        score += 0.08
    return score


def _has_bad_sentence_fragment(text: str) -> bool:
    stripped = " ".join((text or "").split())
    if not stripped:
        return True
    words = _TOKEN_RE.findall(stripped)
    if not words:
        return True
    if re.search(r"[.!?]\s+[a-z]", stripped):
        return True
    if re.search(r"[.!?]\s+[A-Z]", stripped) and not stripped.endswith((".", "!", "?")):
        return True
    if words[-1].casefold() in _WEAK_BOUNDARY_WORDS:
        return True
    if re.search(r"\b(?:and|or|but|because|since|if|when|while|with|for|to|of|in|on|by)$", stripped, re.I):
        return True
    return False


def _next_non_space_index(text: str, start: int) -> int:
    idx = max(0, start)
    while idx < len(text) and text[idx].isspace():
        idx += 1
    return idx


def _extend_quote_end_to_boundary(quote: str, end: int, max_extra_chars: int = 110) -> int:
    if not quote:
        return end
    idx = _next_non_space_index(quote, end)
    limit = min(len(quote), end + max_extra_chars)
    if idx >= limit:
        return end
    while idx < limit:
        char = quote[idx]
        if char in ".!?\n":
            return idx + 1
        idx += 1
    return end


def _overlap_ratio(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    overlap = max(0, min(a_end, b_end) - max(a_start, b_start))
    if overlap <= 0:
        return 0.0
    denom = max(1, min(a_end - a_start, b_end - b_start))
    return overlap / denom


def _span_selection_score(span: ClaimSpan) -> float:
    length = max(1, span.quote_end - span.quote_start)
    length_penalty = min(0.35, length / 450)
    return (
        _span_quality(span.description_unit, span.quote_span_text)
        + _boundary_quality_bonus(span.quote_span_text)
        - length_penalty
    )


def _dedupe_group_spans(spans: list[ClaimSpan]) -> list[ClaimSpan]:
    deduped: list[ClaimSpan] = []
    for span in sorted(spans, key=lambda s: (s.group_id, s.quote_start, -(s.quote_end - s.quote_start))):
        replaced = False
        for idx, kept in enumerate(deduped):
            if kept.group_id != span.group_id:
                continue
            ratio = _overlap_ratio(
                span.quote_start,
                span.quote_end,
                kept.quote_start,
                kept.quote_end,
            )
            same_desc = span.description_start == kept.description_start and span.description_end == kept.description_end
            if ratio < 0.48 and not same_desc:
                continue
            kept_quality = _span_selection_score(kept)
            span_quality = _span_selection_score(span)
            kept_len = kept.quote_end - kept.quote_start
            span_len = span.quote_end - span.quote_start
            if span_quality > kept_quality or (span_quality == kept_quality and span_len < kept_len):
                deduped[idx] = span
            replaced = True
            break
        if not replaced:
            deduped.append(span)
    return deduped


def _trim_quote_span_boundaries(
    quote: str,
    span: ClaimSpan,
    *,
    strict_fragments: bool = True,
) -> ClaimSpan | None:
    start = max(0, min(span.quote_start, len(quote)))
    end = max(0, min(span.quote_end, len(quote)))
    if end <= start:
        return None

    while start < end and quote[start].isspace():
        start += 1
    while end > start and quote[end - 1].isspace():
        end -= 1

    # Aggressive filler stripping at the start
    current_text = quote[start:end]
    for pattern in _FILLER_START_PATTERNS:
        match = pattern.match(current_text)
        if match:
            start += match.end()
            while start < end and quote[start].isspace():
                start += 1
            current_text = quote[start:end]
            break

    # Find and cut at sentence boundaries for transitional words
    # Cut before words like "Unfortunately", "However", "Moreover" that start new sentences
    _TRANSITIONAL_STARTERS = {"unfortunately", "however", "moreover", "furthermore", "indeed", "additionally", "consequently", "therefore"}
    search_start = start
    while search_start < end - 10:
        # Look for period/newline followed by transitional word
        match = re.search(r'[.!?,;:\n]\s+(\w+)', quote[search_start:end])
        if not match:
            break
        word = match.group(1).casefold()
        if word in _TRANSITIONAL_STARTERS:
            # Cut at the end of the sentence before the transitional word
            end = search_start + match.start() + 1
            while end > start and quote[end - 1].isspace():
                end -= 1
            break
        search_start += match.end()

    changed = True
    while changed and end > start:
        changed = False
        words = list(_TOKEN_RE.finditer(quote[start:end]))
        if not words:
            return None
        first = words[0]
        last = words[-1]
        if first.group(0).casefold() in _WEAK_BOUNDARY_WORDS and len(words) > 3:
            start += first.end()
            while start < end and quote[start] in " \t\r\n,;:—–-":
                start += 1
            changed = True
            continue
        if last.group(0).casefold() in _WEAK_BOUNDARY_WORDS and len(words) > 3:
            end = start + last.start()
            while end > start and quote[end - 1] in " \t\r\n,;:—–-":
                end -= 1
            changed = True

    if end > start:
        current = quote[start:end].rstrip()
        if current and current[-1] not in ".!?" and end < len(quote):
            next_idx = _next_non_space_index(quote, end)
            if next_idx < len(quote) and quote[next_idx].isalpha() and quote[next_idx].islower():
                extended_end = _extend_quote_end_to_boundary(quote, end)
                if extended_end > end:
                    end = extended_end
                    while end > start and quote[end - 1].isspace():
                        end -= 1

    text = quote[start:end].strip()
    if not _span_word_count_ok(text):
        return None
    if not _has_substantive_content(text):
        return None
    if strict_fragments and _has_bad_sentence_fragment(text):
        return None
    return span.model_copy(update={"quote_start": start, "quote_end": end, "quote_span_text": quote[start:end]})


def _cleanup_claim_spans(
    quote: str,
    spans: list[ClaimSpan],
    *,
    allow_lenient_rescue: bool = False,
) -> list[ClaimSpan]:
    cleaned: list[ClaimSpan] = []
    for span in spans:
        trimmed = _trim_quote_span_boundaries(quote, span, strict_fragments=True)
        if trimmed is not None:
            cleaned.append(trimmed)

    if not cleaned and allow_lenient_rescue and spans:
        rescue: list[ClaimSpan] = []
        for span in spans:
            trimmed = _trim_quote_span_boundaries(quote, span, strict_fragments=False)
            if trimmed is not None:
                rescue.append(trimmed)
        rescue = _dedupe_group_spans(rescue)
        if rescue:
            rescue_sorted = sorted(
                rescue,
                key=lambda s: (-_span_selection_score(s), s.quote_start, s.quote_end),
            )
            top = rescue_sorted[0]
            same_group = [s for s in rescue_sorted[1:] if s.group_id == top.group_id]
            return sorted([top, *same_group[:1]], key=lambda s: (s.quote_start, s.description_start))

    cleaned = _dedupe_group_spans(cleaned)
    selected: list[ClaimSpan] = []
    for span in sorted(cleaned, key=lambda s: (-_span_selection_score(s), s.quote_start, s.quote_end)):
        keep = True
        for kept in selected:
            ratio = _overlap_ratio(span.quote_start, span.quote_end, kept.quote_start, kept.quote_end)
            same_group = span.group_id == kept.group_id
            same_desc = span.description_start == kept.description_start and span.description_end == kept.description_end
            if ratio < (0.35 if same_group or same_desc else 0.55):
                continue
            if same_group or same_desc:
                keep = False
                break
            if _span_selection_score(span) <= _span_selection_score(kept) + 0.08:
                keep = False
                break
        if keep:
            selected.append(span)
    return sorted(selected, key=lambda s: (s.quote_start, s.description_start))


def _validate_mapped_spans(
    description: str,
    quote: str,
    spans: list[ClaimSpan],
) -> list[ClaimSpan]:
    """Keep only spans with valid description<->quote mapping integrity."""
    validated: list[ClaimSpan] = []
    desc_len = len(description or "")
    quote_len = len(quote or "")

    for span in spans:
        d_start = max(0, min(int(span.description_start), desc_len))
        d_end = max(0, min(int(span.description_end), desc_len))
        q_start = max(0, min(int(span.quote_start), quote_len))
        q_end = max(0, min(int(span.quote_end), quote_len))
        if d_end <= d_start or q_end <= q_start:
            continue

        desc_text = (description[d_start:d_end] or "").strip()
        quote_text = (quote[q_start:q_end] or "").strip()
        if not desc_text or not quote_text:
            continue
        if not _span_word_count_ok(desc_text) or not _span_word_count_ok(quote_text):
            continue
        if not _has_substantive_content(desc_text) or not _has_substantive_content(quote_text):
            continue

        validated.append(
            span.model_copy(
                update={
                    "description_start": d_start,
                    "description_end": d_end,
                    "quote_start": q_start,
                    "quote_end": q_end,
                    "description_unit": desc_text,
                    "quote_span_text": quote_text,
                }
            )
        )

    return _dedupe_group_spans(validated)


def _unique_group_count(spans: list[ClaimSpan]) -> int:
    return len({span.group_id for span in spans if span.group_id})


def _should_attempt_recovery(description: str, spans: list[ClaimSpan]) -> bool:
    if not spans:
        return True
    return _unique_group_count(spans) < min(2, _target_group_count(description))


def _next_group_index(spans: list[ClaimSpan]) -> int:
    highest = 0
    for span in spans:
        match = re.fullmatch(r"g(\d+)", span.group_id or "")
        if not match:
            continue
        highest = max(highest, int(match.group(1)))
    return highest + 1


def _get_thresholds_for_level(coloring_level: str) -> tuple[float, float, float]:
    """Return (same_claim_min, substance_min, boundary_min) for given coloring level."""
    if coloring_level == "minimal":
        # Strict: only the strongest matches
        return (0.60, 0.60, 0.50)
    elif coloring_level == "dense":
        # High-recall: maximize concept linkage coverage
        return (0.35, 0.35, 0.25)
    elif coloring_level == "maximum":
        # Permissive: accept more spans
        return (0.45, 0.45, 0.35)
    else:  # medium (default)
        return (0.52, 0.52, 0.42)


async def _verify_candidate_spans_async(
    client: AsyncOpenAI,
    description: str,
    quote: str,
    spans: list[ClaimSpan],
    coloring_level: str = "medium",
) -> list[ClaimSpan]:
    if not spans:
        return []

    payload_items = [
        {
            "index": idx,
            "description_span": span.description_unit,
            "quote_span": span.quote_span_text,
            "group_id": span.group_id,
        }
        for idx, span in enumerate(spans)
    ]
    payload_text = json.dumps(payload_items, ensure_ascii=True)

    def _collect_verified(decisions: dict[int, bool]) -> list[ClaimSpan]:
        verified: list[ClaimSpan] = []
        for idx, span in enumerate(spans):
            accepted = decisions.get(idx)
            if accepted is None:
                continue
            if not accepted:
                continue
            verified.append(span)
        return verified

    # Get thresholds based on coloring level
    same_claim_min, substance_min, boundary_min = _get_thresholds_for_level(coloring_level)

    async def _request_decisions() -> dict[int, bool] | None:
        # Adjust prompt guidance based on coloring level
        if coloring_level == "minimal":
            level_guidance = (
                "- accept=true only when same_claim_aspect >= 0.60, substance >= 0.60, and boundary_quality >= 0.50.\n"
                "- Be STRICT: prefer high precision over recall. Only accept the strongest semantic matches.\n"
            )
        elif coloring_level == "dense":
            level_guidance = (
                "- accept=true when same_claim_aspect >= 0.35, substance >= 0.35, and boundary_quality >= 0.25.\n"
                "- Be HIGH-RECALL: maximize concept coverage. Accept looser but plausible semantic links, paraphrases, related-evidence mappings, and partial concept overlaps.\n"
            )
        elif coloring_level == "maximum":
            level_guidance = (
                "- accept=true when same_claim_aspect >= 0.45, substance >= 0.45, and boundary_quality >= 0.35.\n"
                "- Be PERMISSIVE: maximize coverage even for weaker semantic matches. Include related-evidence mappings.\n"
            )
        else:  # medium
            level_guidance = (
                "- accept=true only when same_claim_aspect >= 0.55, substance >= 0.55, and boundary_quality >= 0.45.\n"
                "- Prefer precision, but keep valid related-evidence mappings (not only literal repeats).\n"
            )

        prompt = (
            "You are a semantic evidence verifier.\n"
            "Evaluate each candidate (description_span, quote_span) pair.\n"
            "Return JSON only with this exact shape:\n"
            "{\"items\":[{\"index\":0,\"same_claim_aspect\":0.0,\"substance\":0.0,\"boundary_quality\":0.0,\"accept\":true}]}\n\n"
            "Scoring dimensions (0.0 to 1.0):\n"
            "- same_claim_aspect: both spans refer to the same concrete claim aspect.\n"
            "- substance: quote_span contains meaningful evidence, not shell/opening filler.\n"
            "- boundary_quality: quote_span boundaries are reasonably tight and not overly broad.\n\n"
            "Acceptance rule guidance:\n"
            + level_guidance +
            "- Treat topic as the semantic claim topic, not the grammatical subject of a sentence.\n"
            "- Never accept attribution-only, reporting/source phrases, or generic opener text as evidence.\n"
            "- Reject pairs whose main topic is only that a report, document, press outlet, person, or source stated/quoted/reported something.\n"
            "- Reject quote_span that starts with a proper name or person as the grammatical subject (e.g., 'Khamenei was...', 'Mazari opposed...').\n"
            "- EXCEPTION: Accept spans starting with a person if they express alignment (pro-/anti-), relationship (protégé, disciple, ally), sponsorship (supported by, backed by), or ideological positioning claims.\n"
            "- Examples of acceptable person-starting spans: 'Khamenei was strongly pro-Soviet' (alignment), 'Mazari was the protégé of Khamenei' (relationship), 'was supported by the IRGC' (sponsorship).\n"
            "- The claim content (alignment, relationship, sponsorship) is the semantic topic, not the person as a biographical subject.\n\n"
            f"DESCRIPTION:\n{description}\n\n"
            f"QUOTE:\n{quote}\n\n"
            f"CANDIDATES_JSON:\n{payload_text}\n\n"
        )

        response = await client.chat.completions.create(
            model=_VERIFY_MODEL,
            reasoning_effort=_VERIFY_REASONING_EFFORT,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        content = (response.choices[0].message.content or "").strip()
        data = _parse_json_object(content)
        items = data.get("items")
        if not isinstance(items, list):
            return None

        decisions: dict[int, bool] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(spans):
                continue

            same_claim = float(item.get("same_claim_aspect", 0.0) or 0.0)
            substance = float(item.get("substance", 0.0) or 0.0)
            boundary = float(item.get("boundary_quality", 0.0) or 0.0)
            accept_flag = bool(item.get("accept", False))

            same_claim = max(0.0, min(1.0, same_claim))
            substance = max(0.0, min(1.0, substance))
            boundary = max(0.0, min(1.0, boundary))

            hard_min_pass = (
                same_claim >= same_claim_min
                and substance >= substance_min
                and boundary >= boundary_min
            )
            decisions[idx] = accept_flag and hard_min_pass
        return decisions

    strict_decisions = await _request_decisions()
    if strict_decisions is None:
        return spans
    return _collect_verified(strict_decisions)


async def _extract_recovery_claim_spans_async(
    client: AsyncOpenAI,
    description: str,
    quote: str,
    existing_spans: list[ClaimSpan] | None = None,
) -> list[ClaimSpan]:
    covered_items = [
        {
            "description_span": span.description_unit,
            "quote_span": span.quote_span_text,
            "group_id": span.group_id,
        }
        for span in (existing_spans or [])
    ]
    covered_text = json.dumps(covered_items, ensure_ascii=True)
    prompt = (
        "Find high-confidence semantic claim-topic evidence anchor pairs after the normal extractor produced too few accepted highlights.\n"
        "Return JSON only with this exact shape:\n"
        "{\"anchors\":[{\"description_span\":\"...\",\"quote_span\":\"...\"}]}\n\n"
        "Rules:\n"
        "- Return 1 to 3 anchors, or {\"anchors\":[]} if no strong anchors exist.\n"
        "- description_span MUST be an exact contiguous substring of DESCRIPTION.\n"
        "- quote_span MUST be an exact contiguous substring of QUOTE.\n"
        "- Each span should be compact but complete enough to read naturally.\n"
        "- A topic is the semantic claim idea being evidenced, not the grammatical subject of a sentence.\n"
        "- Prefer claim-relevant evidence anchors involving ideology, influence, support, funding, aid, policy, labor, movement, uprising, repression, colonial control, or institutional cooperation.\n"
        "- Accept strong semantic equivalents even when wording differs, such as Bolshevik influence -> developing into Bolshevism, Soviet aid -> Russian Bolshevists promised help, financial support -> money they have given.\n"
        "- Do not return attribution-only, source-introduction, reporting/source, or shell text as an anchor.\n"
        "- Do not use phrases whose main function is only that a report, document, press outlet, person, or source stated/quoted/reported something.\n"
        "- Do not return headings, citation text, or generic document/source phrases.\n"
        "- Do not repeat anchors already covered in COVERED_JSON.\n"
        "- Avoid duplicate or heavily overlapping quote spans.\n\n"
        f"DESCRIPTION:\n{description}\n\n"
        f"QUOTE:\n{quote}\n\n"
        f"COVERED_JSON:\n{covered_text}\n\n"
    )

    response = await client.chat.completions.create(
        model=_EXTRACT_MODEL,
        reasoning_effort=_VERIFY_REASONING_EFFORT,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    content = (response.choices[0].message.content or "").strip()
    data = _parse_json_object(content)
    anchors = data.get("anchors")
    if not isinstance(anchors, list):
        return []

    spans: list[ClaimSpan] = []
    group_by_description: dict[str, tuple[str, str]] = {}
    used_quote_exact: set[tuple[int, int]] = {
        (span.quote_start, span.quote_end) for span in (existing_spans or [])
    }
    next_group_idx = _next_group_index(existing_spans or [])
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        desc_span_text = " ".join(str(anchor.get("description_span", "")).split()).strip()
        quote_span_text = " ".join(str(anchor.get("quote_span", "")).split()).strip()
        if not desc_span_text or not quote_span_text:
            continue
        if not _span_word_count_ok(desc_span_text) or not _span_word_count_ok(quote_span_text):
            continue
        if not _has_substantive_content(desc_span_text) or not _has_substantive_content(quote_span_text):
            continue

        desc_loc = _find_best_span_lenient(description, desc_span_text)
        quote_loc = _find_best_span_lenient(quote, quote_span_text)
        if desc_loc is None or quote_loc is None:
            continue
        d_start, d_end = desc_loc
        q_start, q_end = quote_loc
        if (q_start, q_end) in used_quote_exact:
            continue

        desc_key = description[d_start:d_end].casefold()
        group = group_by_description.get(desc_key)
        if group is None:
            group = (f"g{next_group_idx}", _unit_color(next_group_idx - 1))
            group_by_description[desc_key] = group
            next_group_idx += 1

        spans.append(
            ClaimSpan(
                description_start=d_start,
                description_end=d_end,
                quote_start=q_start,
                quote_end=q_end,
                color=group[1],
                group_id=group[0],
                description_unit=description[d_start:d_end],
                quote_span_text=quote[q_start:q_end],
            )
        )
        used_quote_exact.add((q_start, q_end))
        if len(spans) >= 3:
            break
    return spans


async def _rescue_claim_spans_async(
    client: AsyncOpenAI,
    description: str,
    quote: str,
) -> list[ClaimSpan]:
    rescue_spans: list[ClaimSpan] = []
    used_quote_exact: set[tuple[int, int]] = set()
    candidates = [
        *_shared_description_units(description, quote),
        *_overlap_description_units(description, quote),
        *(await _extract_units(client, description)),
    ]
    seen_desc: set[str] = set()
    next_group_idx = 1
    for unit in candidates:
        if len(rescue_spans) >= 3:
            break
        unit_text = " ".join(str(unit.get("text", "")).split()).strip()
        if not unit_text:
            continue
        if not _span_word_count_ok(unit_text):
            continue
        if not _has_substantive_content(unit_text):
            continue
        key = unit_text.casefold()
        if key in seen_desc:
            continue
        desc_loc = _find_best_span_lenient(description, unit_text)
        if desc_loc is None:
            continue
        d_start, d_end = desc_loc
        desc_span = description[d_start:d_end]
        if not _span_word_count_ok(desc_span):
            continue

        quote_loc = _find_best_span_lenient(quote, unit_text)
        if quote_loc is None:
            continue
        q_start, q_end = _expand_quote_span_for_group(quote, quote_loc[0], quote_loc[1], unit_text)
        if (q_start, q_end) in used_quote_exact:
            continue
        quote_span_text = quote[q_start:q_end]
        if not _span_word_count_ok(quote_span_text):
            continue
        if not _has_substantive_content(quote_span_text):
            continue
        if not (
            _pair_passes_gate("relation", unit_text, quote_span_text)
            or _pair_passes_gate("entity", unit_text, quote_span_text)
            or _related_evidence_gate(unit_text, quote_span_text)
        ):
            continue

        rescue_spans.append(
            ClaimSpan(
                description_start=d_start,
                description_end=d_end,
                quote_start=q_start,
                quote_end=q_end,
                color=_unit_color(next_group_idx - 1),
                group_id=f"g{next_group_idx}",
                description_unit=desc_span,
                quote_span_text=quote_span_text,
            )
        )
        next_group_idx += 1
        used_quote_exact.add((q_start, q_end))
        seen_desc.add(key)
    return rescue_spans


def _dense_candidate_score(description_unit: str, quote_candidate: str) -> float:
    coverage = _token_coverage(description_unit, quote_candidate)
    overlap = _lexical_overlap(description_unit, quote_candidate)
    boundary_bonus = _boundary_quality_bonus(quote_candidate)
    relation_gate = (
        _pair_passes_gate("relation", description_unit, quote_candidate)
        or _pair_passes_gate("entity", description_unit, quote_candidate)
        or _related_evidence_gate(description_unit, quote_candidate)
    )
    return (
        (coverage * 0.60)
        + (overlap * 0.22)
        + (0.16 if relation_gate else 0.0)
        + max(-0.08, min(0.08, boundary_bonus * 0.20))
    )


async def _extract_dense_description_units_async(
    client: AsyncOpenAI,
    description: str,
    quote: str,
) -> list[dict[str, Any]]:
    raw_units = [
        *_quoted_description_units(description),
        *_shared_description_units(description, quote),
        *_overlap_description_units(description, quote),
        *(await _extract_units(client, description)),
    ]

    units: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    used_ranges: list[tuple[int, int]] = []

    for unit in raw_units:
        base_text = " ".join(str(unit.get("text", "")).split()).strip()
        if not base_text:
            continue
        kind = str(unit.get("kind", "relation") or "relation").lower()
        parts = [base_text]
        parts.extend(_split_description_span_candidates(base_text))
        for part in parts:
            text = " ".join((part or "").split()).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen_text:
                continue
            loc = _find_best_span_lenient(description, text)
            if loc is None:
                continue
            d_start, d_end = loc
            desc_text = description[d_start:d_end].strip()
            if not desc_text:
                continue
            if _has_overlap(used_ranges, d_start, d_end):
                continue
            if not _span_word_count_ok(desc_text):
                continue
            if not _has_substantive_content(desc_text):
                continue

            seen_text.add(key)
            used_ranges.append((d_start, d_end))
            units.append(
                {
                    "text": desc_text,
                    "kind": kind if kind in {"entity", "action", "relation"} else "relation",
                    "start": d_start,
                    "end": d_end,
                }
            )
            if len(units) >= _DENSE_MAX_UNITS:
                return units

    units.sort(key=lambda item: int(item.get("start", 0)))
    return units


async def _extract_dense_fallback_spans_async(
    client: AsyncOpenAI,
    description: str,
    quote: str,
    existing_spans: list[ClaimSpan] | None = None,
) -> list[ClaimSpan]:
    units = await _extract_dense_description_units_async(client, description, quote)
    if not units:
        return []

    quote_candidates = _quote_candidates(quote)
    if not quote_candidates:
        return []

    accepted: list[ClaimSpan] = []
    used_quote_exact: set[tuple[int, int]] = {
        (span.quote_start, span.quote_end) for span in (existing_spans or [])
    }
    next_group_idx = _next_group_index(existing_spans or [])
    best_overall: tuple[float, dict[str, Any], tuple[int, int], str] | None = None

    for unit in units:
        if len(accepted) >= _DENSE_MAX_TOTAL_SPANS:
            break

        unit_text = str(unit.get("text", "")).strip()
        d_start = int(unit.get("start", 0))
        d_end = int(unit.get("end", 0))
        if not unit_text or d_end <= d_start:
            continue

        ranked: list[tuple[float, float, float, bool, int, int, str]] = []
        for candidate in quote_candidates:
            q_start = int(candidate.get("start", 0))
            q_end = int(candidate.get("end", 0))
            if q_end <= q_start:
                continue
            q_start, q_end = _expand_quote_span_for_group(quote, q_start, q_end, unit_text)
            if q_end <= q_start:
                continue
            if (q_start, q_end) in used_quote_exact:
                continue

            quote_span_text = (quote[q_start:q_end] or "").strip()
            if not _span_word_count_ok(quote_span_text):
                continue

            score = _dense_candidate_score(unit_text, quote_span_text)
            coverage = _token_coverage(unit_text, quote_span_text)
            overlap = _lexical_overlap(unit_text, quote_span_text)
            passes_relaxed_floor = (
                coverage >= _DENSE_RELAXED_MIN_COVERAGE
                or score >= _DENSE_RELAXED_MIN_SCORE
                or _pair_passes_gate("relation", unit_text, quote_span_text)
                or _pair_passes_gate("entity", unit_text, quote_span_text)
                or _related_evidence_gate(unit_text, quote_span_text)
            )
            substantive = _has_substantive_content(quote_span_text)
            ranked.append((score, coverage, overlap, passes_relaxed_floor and substantive, q_start, q_end, quote_span_text))

            if best_overall is None or score > best_overall[0]:
                best_overall = (score, unit, (q_start, q_end), quote_span_text)

        if not ranked:
            continue

        ranked.sort(key=lambda item: (-int(item[3]), -item[0], -item[1], -item[2], item[4], item[5] - item[4]))
        best_matches = [item for item in ranked if item[3]][:_DENSE_TOP_K_PER_UNIT]
        if not best_matches:
            continue

        selected: list[tuple[float, float, float, bool, int, int, str]] = []
        selected_ranges: set[tuple[int, int]] = set()
        for item in best_matches:
            selected.append(item)
            selected_ranges.add((item[4], item[5]))
            repeat_spans = _find_exact_repeat_spans(
                quote,
                item[6],
                max_matches=_DENSE_MAX_REPEATS_PER_UNIT + 1,
            )
            for repeat_start, repeat_end in repeat_spans:
                if (repeat_start, repeat_end) in selected_ranges:
                    continue
                if (repeat_start, repeat_end) in used_quote_exact:
                    continue
                repeat_text = (quote[repeat_start:repeat_end] or "").strip()
                if not repeat_text:
                    continue
                selected.append((item[0], item[1], item[2], item[3], repeat_start, repeat_end, repeat_text))
                selected_ranges.add((repeat_start, repeat_end))
                if len(selected_ranges) >= _DENSE_MAX_REPEATS_PER_UNIT + 1:
                    break

        group_id = f"g{next_group_idx}"
        group_color = _unit_color(next_group_idx - 1)
        next_group_idx += 1

        for _, _, _, _, q_start, q_end, quote_span_text in selected:
            if len(accepted) >= _DENSE_MAX_TOTAL_SPANS:
                break
            if (q_start, q_end) in used_quote_exact:
                continue
            accepted.append(
                ClaimSpan(
                    description_start=d_start,
                    description_end=d_end,
                    quote_start=q_start,
                    quote_end=q_end,
                    color=group_color,
                    group_id=group_id,
                    description_unit=unit_text,
                    quote_span_text=quote_span_text,
                )
            )
            used_quote_exact.add((q_start, q_end))

    if accepted:
        return accepted

    # Dense mode policy: if no relaxed-floor pair survived, force best available
    # candidate while preserving description->quote mapping integrity.
    if best_overall is None:
        return []

    _, unit, (q_start, q_end), quote_span_text = best_overall
    d_start = int(unit.get("start", 0))
    d_end = int(unit.get("end", 0))
    unit_text = str(unit.get("text", "")).strip()
    if d_end <= d_start or q_end <= q_start or not unit_text or not quote_span_text:
        return []
    return [
        ClaimSpan(
            description_start=d_start,
            description_end=d_end,
            quote_start=q_start,
            quote_end=q_end,
            color=_unit_color(0),
            group_id="g1",
            description_unit=unit_text,
            quote_span_text=quote_span_text,
        )
    ]


async def _extract_semantic_groups(
    client: AsyncOpenAI,
    description_text: str,
    quote_text: str,
    coloring_level: str = "medium",
) -> list[dict[str, Any]]:
    sentence_count = len([s for s in re.split(r"(?<=[.!?;:])\s+", description_text or "") if s.strip()])
    token_count = len(_TOKEN_RE.findall(description_text or ""))
    if sentence_count >= 5 or token_count >= 120:
        target_groups = 5
    elif sentence_count >= 3 or token_count >= 70:
        target_groups = 4
    else:
        target_groups = 2

    if coloring_level == "dense":
        target_groups += 1

    dense_rule_extra = ""
    if coloring_level == "dense":
        dense_rule_extra = (
            "- Dense mode: prioritize broader concept coverage across entities, actions, and key phrases.\n"
            "- Prefer more unique description concepts over multiple distinct quote excerpts for the same concept.\n"
            "- For each description concept, return the single best quote_phrase; include additional quote_phrases only when they are true repetitions of the same excerpt/concept.\n"
        )

    prompt = (
        "Link QUOTE phrases to DESCRIPTION topic spans semantically.\n"
        "For parsing quotes, identify topics in DESCRIPTION (X, Y, Z, ...), then\n"
        "map each topic to corresponding concept-phrases in QUOTE.\n"
        "Return JSON only with this exact shape:\n"
        "{\"groups\":[{\"topic\":\"...\",\"description_span\":\"...\",\"quote_phrases\":[\"...\", \"...\"]}]}\n\n"
        "Internal process :\n"
        "1) Entity Identification: list unique entities, dates, document names, and\n"
        "   technical/political terms from DESCRIPTION span candidates.\n"
        "2) Quote Segmentation: split QUOTE into semantic segments (sentences or\n"
        "   clause blocks) instead of treating QUOTE as one undivided block.\n"
        "3) Strict Anchor Matching: for each identified entity/term, locate matching\n"
        "   occurrences across segments; if mentioned multiple times, include all\n"
        "   meaningful occurrences up to the row limits (solve 1:N mapping).\n"
        "4) Filler Elimination: discard shell/opening phrases and attribution-only\n"
        "   context before selecting final quote_phrases.\n"
        "5) Validation: keep only phrases that contain substantive claim evidence,\n"
        "   not merely introductory wording.\n\n"
        "Rules:\n"
        "- Number of groups must be dynamic based on content; never force a fixed\n"
        "  count such as always 3.\n"
        f"- For this row, target up to {target_groups} groups when each group is\n"
        "  directly related to a concrete claim aspect in DESCRIPTION.\n"
        "- Prefer useful related highlights over sparse output, but never highlight\n"
        "  generic filler text.\n"
        + dense_rule_extra +
        "- Create a new group whenever the topic changes materially.\n"
        "- topic should be a short label (2-6 words) describing the theme.\n"
        "- topic means the semantic claim topic, not the grammatical subject of a sentence.\n"
        "- Do not create topics from reporting/source phrases such as 'the report stated', 'the press reports stated', 'quoted by', or 'in the words of'.\n"
        "- description_span MUST be at least 3 words. Single-word description_spans\n"
        "  are forbidden (they produce trivial word-repetition matches).\n"
        "- description_span should be a contiguous span from DESCRIPTION.\n"
        "- description_span can be multi-sentence (recommended when needed).\n"
        "- Each quote_phrases item should correspond to a contiguous phrase in QUOTE.\n"
        "- quote_phrases MUST be at least 3 words. Single-word quote_phrases are\n"
        "  forbidden (they are mere word repetitions, not semantic links).\n"
        "- Use concept consistency: each group/color must represent one claim aspect,\n"
        "  entity, person, event, policy, movement, or outcome. Quote phrases may be\n"
        "  direct equivalents, strong synonyms, or directly related evidence phrases\n"
        "  for that same claim aspect.\n"
        "  BAD: a source title in DESCRIPTION → an unrelated generic struggle phrase\n"
        "  in QUOTE (different concept categories).\n"
        "  GOOD: an organization/entity phrase in DESCRIPTION → the same\n"
        "  organization/entity under alternate naming in QUOTE.\n"
        "- A single group/color must represent one specific entity, person, or\n"
        "  specific ideology throughout this link. Never reuse a color/group for\n"
        "  another category of information.\n"
        "- Treat each group as one unique description topic (unique color group).\n"
        "- A description concept may map to multiple quote_phrases.\n"
        "- If a quote phrase lacks a strong conceptual anchor in DESCRIPTION,\n"
        "  leave it unhighlighted.\n"
        "- Good related-evidence mappings are allowed when both sides express the\n"
        "  same claim aspect, even with different wording. Examples:\n"
        "  movement/uprising claim → unrest, agitation, revolt, mobilization;\n"
        "  independence/sovereignty claim → declaration, recognition, self-rule;\n"
        "  control/domination claim → protectorate, occupation, supervision,\n"
        "  imposed authority, reserved powers;\n"
        "  party/organization claim → the same organization under an alternate name,\n"
        "  abbreviation, faction label, or leadership reference;\n"
        "  foreign support/intervention claim → aid, backing, training, funding,\n"
        "  arms, advisers, missions, diplomatic pressure;\n"
        "  ideological influence claim → doctrine, propaganda, radicalization,\n"
        "  political current, ideological tendency, movement influence;\n"
        "  opposition/conflict claim → resistance, counter-movement, suppression,\n"
        "  confrontation, crackdown, rivalry;\n"
        "  leadership/person claim → the named person, title, role, office,\n"
        "  command responsibility, or actions attributed to that person;\n"
        "  document/source claim → the same report, memorandum, cable, bulletin,\n"
        "  article, decree, statement, or identifiable document title;\n"
        "  policy/decision claim → proposal, declaration, agreement, order,\n"
        "  recommendation, condition, measure, reform, or official position;\n"
        "  economic/social claim → finance, labor, class, land, production,\n"
        "  trade, education, social base, popular support;\n"
        "  location/territory claim → the same country, region, city, province,\n"
        "  border area, colony, protectorate, or administered territory.\n"
        "- Never use shell/generic openers as anchors by themselves, such as\n"
        "  'Further evidence has accumulated', 'according to the document',\n"
        "  'the situation was as follows', 'the press reports stated that',\n"
        "  'in the words of', or 'it was stated'.\n"
        "- Do not include headings/titles.\n"
        "- Do not return duplicate groups or duplicate quote_phrases in a group.\n"
        "- If no valid mapping exists, return {\"groups\":[]}.\n\n"
        f"DESCRIPTION:\n{description_text}\n\n"
        f"QUOTE:\n{quote_text}\n\n"
    )

    response = await client.chat.completions.create(
        model=_EXTRACT_MODEL,
        reasoning_effort=_EXTRACT_REASONING_EFFORT,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    content = (response.choices[0].message.content or "").strip()
    data = _parse_json_object(content)
    groups_raw = data.get("groups")
    if not isinstance(groups_raw, list):
        return []

    groups: list[dict[str, Any]] = []
    seen_desc: set[str] = set()
    for group in groups_raw:
        if not isinstance(group, dict):
            continue
        topic = " ".join(str(group.get("topic", "")).split()).strip()
        desc_span = " ".join(str(group.get("description_span", "")).split()).strip()
        if not desc_span:
            continue
        desc_key = desc_span.casefold()
        if desc_key in seen_desc:
            continue
        seen_desc.add(desc_key)

        raw_phrases = group.get("quote_phrases")
        if not isinstance(raw_phrases, list):
            continue

        phrases: list[str] = []
        seen_phrase: set[str] = set()
        for phrase in raw_phrases:
            text = " ".join(str(phrase).split()).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen_phrase:
                continue
            seen_phrase.add(key)
            phrases.append(text)

        if not phrases:
            continue
        groups.append({"topic": topic, "description_span": desc_span, "quote_phrases": phrases})
    return groups


async def _augment_groups_to_minimum(
    client: AsyncOpenAI,
    description: str,
    quote: str,
    groups: list[dict[str, Any]],
    target_groups: int,
) -> list[dict[str, Any]]:
    if len(groups) >= target_groups:
        return groups

    existing_desc = {" ".join(str(g.get("description_span", "")).split()).casefold() for g in groups}
    candidates = [
        *_quoted_description_units(description),
        *_shared_description_units(description, quote),
        *_overlap_description_units(description, quote),
        *(await _extract_units(client, description)),
    ]

    _MIN_WORDS = 3  # Single-word anchors produce trivial word-repetition matches.
    for unit in candidates:
        if len(groups) >= target_groups:
            break
        unit_text = " ".join(str(unit.get("text", "")).split()).strip()
        if not unit_text:
            continue
        if len(_TOKEN_RE.findall(unit_text)) < _MIN_WORDS:
            continue
        key = unit_text.casefold()
        if key in existing_desc:
            continue
        q_loc = _find_best_span_lenient(quote, unit_text)
        if q_loc is None:
            continue
        q_start, q_end = q_loc
        quote_phrase = quote[q_start:q_end].strip()
        if not quote_phrase:
            continue
        if len(_TOKEN_RE.findall(quote_phrase)) < _MIN_WORDS:
            continue
        groups.append(
            {
                "topic": unit_text,
                "description_span": unit_text,
                "quote_phrases": [quote_phrase],
            }
        )
        existing_desc.add(key)
    return groups


def _target_group_count(description: str) -> int:
    sentence_count = len([s for s in re.split(r"(?<=[.!?;:])\s+", description or "") if s.strip()])
    token_count = len(_TOKEN_RE.findall(description or ""))
    if sentence_count >= 5 or token_count >= 120:
        return 5
    if sentence_count >= 3 or token_count >= 70:
        return 4
    return 2


async def _process_link_async(
    client: AsyncOpenAI,
    link: QuoteImageLink,
    *,
    forced_description: tuple[str, int] | None = None,
    coloring_level: str = "medium",
) -> None:
    link.claim_spans = []
    # Prefer a borrowed description when set (the visible description is too short
    # to host meaningful highlights). The DOCX writer applies description-side
    # spans to whichever paragraph corresponds to this text.
    if forced_description:
        description, desc_position = forced_description
        link.colorization_description_text = description
        link.colorization_description_position = desc_position
    else:
        description = (
            (link.colorization_description_text or link.description_text) or ""
        ).strip()
    quote = (link.quote_text or "").strip()
    if not description or not quote:
        return

    try:
        semantic_groups = await _extract_semantic_groups(
            client,
            description,
            quote,
            coloring_level=coloring_level,
        )
        if not semantic_groups:
            semantic_groups = await _augment_groups_to_minimum(
                client, description, quote, semantic_groups, target_groups=2
            )

        spans: list[ClaimSpan] = []
        used_quote_exact: set[tuple[int, int]] = set()
        next_group_idx = 1

        for group in semantic_groups:
            desc_span_text = str(group.get("description_span", "")).strip()
            if not desc_span_text:
                continue

            quote_phrases = group.get("quote_phrases")
            if not isinstance(quote_phrases, list):
                continue

            desc_loc = _find_best_span_lenient(description, desc_span_text)
            if desc_loc is None:
                continue
            d_start, d_end = desc_loc
            desc_span = description[d_start:d_end]
            # Reject description spans shorter than 3 words — they are trivial
            # word-repetition matches, not semantic links.
            if not _span_word_count_ok(desc_span):
                continue
            if not _has_substantive_content(desc_span):
                continue

            group_id = f"g{next_group_idx}"
            group_color = _unit_color(next_group_idx - 1)
            next_group_idx += 1
            group_match_count = 0
            if coloring_level == "dense":
                group_match_limit = min(
                    _DENSE_MAX_REPEATS_PER_UNIT + 1,
                    _MAX_MIRRORS_PER_GROUP,
                )
            else:
                group_match_limit = min(_max_matches_per_unit(quote), _MAX_MIRRORS_PER_GROUP)
            alias_spans = _alias_mirror_spans(
                quote,
                _group_aliases(desc_span_text, quote_phrases),
                max_matches=group_match_limit,
                used_quote_exact=used_quote_exact,
            )

            if coloring_level == "dense":
                quote_targets: list[str | tuple[int, int]] = [str(phrase) for phrase in quote_phrases]
            else:
                quote_targets = [
                    *[str(phrase) for phrase in quote_phrases],
                    *alias_spans,
                ]

            for phrase_text in quote_targets:
                if group_match_count >= group_match_limit:
                    break
                if isinstance(phrase_text, tuple):
                    phrase_spans = [phrase_text]
                else:
                    phrase_text = " ".join(str(phrase_text).split()).strip()
                    if not phrase_text:
                        continue
                    phrase_spans = _find_spans_for_phrase(
                        quote,
                        phrase_text,
                        max_matches=group_match_limit - group_match_count,
                    )
                if not phrase_spans:
                    continue
                for q_start, q_end in phrase_spans:
                    q_start, q_end = _expand_quote_span_for_group(
                        quote,
                        q_start,
                        q_end,
                        desc_span_text,
                    )
                    # Reject quote spans shorter than 3 words — trivial repetition.
                    quote_span_text = quote[q_start:q_end]
                    if not _span_word_count_ok(quote_span_text):
                        continue
                    if not _has_substantive_content(quote_span_text):
                        continue
                    # Accept if either relation-like or entity-like lexical gate passes.
                    if not (
                        _pair_passes_gate("relation", desc_span_text, quote_span_text)
                        or _pair_passes_gate("entity", desc_span_text, quote_span_text)
                        or _related_evidence_gate(desc_span_text, quote_span_text)
                    ):
                        continue
                    if (q_start, q_end) in used_quote_exact:
                        continue
                    spans.append(
                        ClaimSpan(
                            description_start=d_start,
                            description_end=d_end,
                            quote_start=q_start,
                            quote_end=q_end,
                            color=group_color,
                            group_id=group_id,
                            description_unit=desc_span_text,
                            quote_span_text=quote_span_text,
                        )
                    )
                    used_quote_exact.add((q_start, q_end))
                    group_match_count += 1
                    if group_match_count >= group_match_limit:
                        break
                if coloring_level == "dense" and group_match_count > 0:
                    break

        spans = _dedupe_group_spans(spans)
        if spans:
            spans = await _verify_candidate_spans_async(client, description, quote, spans, coloring_level=coloring_level)
            spans = _cleanup_claim_spans(quote, spans)
        if _should_attempt_recovery(description, spans):
            recovery_spans = await _extract_recovery_claim_spans_async(
                client,
                description,
                quote,
                existing_spans=spans,
            )
            recovery_spans = _dedupe_group_spans(recovery_spans)
            if recovery_spans:
                recovery_spans = await _verify_candidate_spans_async(
                    client,
                    description,
                    quote,
                    recovery_spans,
                    coloring_level=coloring_level,
                )
                spans = _dedupe_group_spans([*spans, *recovery_spans])
        spans = _cleanup_claim_spans(quote, spans, allow_lenient_rescue=True)
        spans = _validate_mapped_spans(description, quote, spans)

        # Dense mode stability: if a valid description ended empty, perform a
        # deterministic, description-origin fallback with controlled relaxation.
        if coloring_level == "dense" and _is_valid_description_for_mapping(description) and not spans:
            dense_spans = await _extract_dense_fallback_spans_async(
                client,
                description,
                quote,
                existing_spans=spans,
            )
            dense_spans = _dedupe_group_spans(dense_spans)
            dense_spans = _cleanup_claim_spans(quote, dense_spans, allow_lenient_rescue=True)
            spans = _validate_mapped_spans(description, quote, dense_spans)

        link.claim_spans = spans
    except _FATAL_OPENAI_ERRORS:
        # Bubble fatal API errors so the API layer can return a clear message
        # (quota exceeded, invalid key, network down, etc.).
        raise
    except APIError as exc:
        logger.warning("OpenAI APIError during span colorization: %s", exc)
        link.claim_spans = []
    except Exception as exc:
        logger.exception("Unexpected error during span colorization: %s", exc)
        link.claim_spans = []


def _has_description_side_spans(link: QuoteImageLink) -> bool:
    return any(
        (span.description_end or 0) > (span.description_start or 0)
        for span in (link.claim_spans or [])
    )


async def _run_all_async(quote_links: list[QuoteImageLink], coloring_level: str = "medium") -> None:
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    async with AsyncOpenAI(api_key=settings.openai_api_key) as client:

        # Pre-process: identify weak descriptions and apply fallback to previous valid description
        # Walk back through previous links until a valid highlighted description is found.
        for idx, link in enumerate(quote_links):
            if idx == 0:
                continue

            current_desc = (link.colorization_description_text or link.description_text or "").strip()

            if _is_weak_description(current_desc):
                # Find previous valid description by walking backward
                for back_idx in range(idx - 1, -1, -1):
                    prev = quote_links[back_idx]
                    prev_desc = (prev.colorization_description_text or prev.description_text or "").strip()

                    # Only use if previous is not weak and has spans
                    if _is_weak_description(prev_desc):
                        continue
                    if not _has_description_side_spans(prev):
                        continue

                    # Borrow whichever paragraph prev's spans actually render on
                    borrow_text = (
                        prev.colorization_description_text or prev.description_text
                    )
                    borrow_position = (
                        prev.colorization_description_position
                        if prev.colorization_description_text
                        else prev.description_position
                    )

                    if borrow_text:
                        link.colorization_description_text = borrow_text
                        link.colorization_description_position = borrow_position
                        break

        async def guarded(link: QuoteImageLink) -> None:
            async with sem:
                await _process_link_async(client, link, coloring_level=coloring_level)

        await asyncio.gather(*(guarded(link) for link in quote_links))

        # Look-back retry: any link that ended up with zero description-side
        # spans walks backward through prior links until it finds one whose
        # own description currently shows highlights, then borrows that
        # description for a second colorization pass. If walk-back finds
        # nothing, the link is left untouched.
        # Skip retry for "minimal" coloring level (strict mode)
        if coloring_level != "minimal":
            retry_links: list[QuoteImageLink] = []
            for idx, link in enumerate(quote_links):
                if idx == 0:
                    continue
                if _has_description_side_spans(link):
                    continue
                borrow_text: str | None = None
                borrow_position: int | None = None
                for back_idx in range(idx - 1, -1, -1):
                    prev = quote_links[back_idx]
                    if not _has_description_side_spans(prev):
                        continue
                    # Borrow whichever paragraph prev's spans actually render on:
                    # its colorization description if it itself borrowed, else
                    # its own description.
                    borrow_text = (
                        prev.colorization_description_text or prev.description_text
                    )
                    borrow_position = (
                        prev.colorization_description_position
                        if prev.colorization_description_text
                        else prev.description_position
                    )
                    if borrow_text:
                        break
                if not borrow_text:
                    continue
                link.colorization_description_text = borrow_text
                link.colorization_description_position = borrow_position
                retry_links.append(link)

            if retry_links:
                await asyncio.gather(*(guarded(link) for link in retry_links))


def assign_claim_colors(quote_links: list[QuoteImageLink], coloring_level: str = "medium") -> None:
    """
    Mutate quote links in place by attaching claim_spans.
    Falls back to empty spans on any LLM or parsing failure.

    Args:
        coloring_level: "minimal" (strict/precise), "medium" (balanced),
            "maximum" (more highlights), or "dense" (high recall)
    """
    for link in quote_links:
        link.claim_spans = []
    if not quote_links:
        return

    def _run_isolated_loop() -> None:
        asyncio.run(_run_all_async(quote_links, coloring_level=coloring_level))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _run_isolated_loop()
    else:
        # FastAPI (and other async callers) already have a loop; asyncio.run is invalid.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_run_isolated_loop).result()
