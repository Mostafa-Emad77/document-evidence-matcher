"""
LLM-assisted matching between HTML citation groups and screenshot pages from
an uploaded PDF document.
"""
from __future__ import annotations

import json
import math
import re
from typing import Optional

from openai import OpenAI

from backend.config import settings
from backend.schemas import CitationGroup, ExtractedImage
from backend.services.pdf_screenshot_extractor import PDFPageEvidence

_client = OpenAI(api_key=settings.openai_api_key)
_EMBED_MODEL = "text-embedding-3-large"
_TOP_K = 6
_LLM_MIN_CONFIDENCE = 0.5
_MIN_COMBINED_SCORE = 0.36
_MIN_SCORE_MARGIN = 0.04
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "into", "about", "under", "over", "between", "their", "there", "they",
    "his", "her", "its", "our", "your", "you", "not", "but", "than", "then",
    "also", "have", "has", "had", "been", "being", "can", "could", "would",
    "should", "may", "might", "to", "of", "in", "on", "at", "as", "by", "or",
    "an", "a", "is", "it",
}
_FINGERPRINT_STOPWORDS = _STOPWORDS | {
    "after", "already", "american", "article", "britain", "british", "bulletin",
    "chapter", "comment", "communism", "communist", "conference", "description",
    "document", "eastern", "egypt", "egyptian", "evidence", "file", "francis",
    "international", "journal", "march", "middle", "necessary", "page", "papers",
    "press", "report", "review", "secret", "security", "society", "sources",
    "studies", "sudan", "taylor", "university", "vol", "volume", "wilson",
    "woodrow",
    "january", "february", "april", "june", "july", "august", "september",
    "october", "november", "december",
}
_YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}\b")
_PROPER_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z]+(?:[-'][A-Z]?[A-Za-z]+)?\b")
_ACRONYM_RE = re.compile(r"\b[A-Z0-9]{2,6}\b")
_DOC_NUMBER_PATTERNS = (
    re.compile(r"\bNo\.?\s*\d+[A-Za-z]?\b", re.IGNORECASE),
    re.compile(r"\bVol\.?\s*\d+[A-Za-z]?\b", re.IGNORECASE),
    re.compile(r"\bpp?\.?\s*\d+[A-Za-z]?(?:\s*[-–]\s*\d+[A-Za-z]?)?\b", re.IGNORECASE),
    re.compile(r"\b\d{1,3}/\d{1,3}(?:/\d{1,3})?\b"),
    re.compile(r"\bChapter\s+\d+[A-Za-z]?\b", re.IGNORECASE),
)


def _embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    resp = _client.embeddings.create(model=_EMBED_MODEL, input=texts)
    return [item.embedding for item in sorted(resp.data, key=lambda x: x.index)]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _tokenize(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {tok for tok in toks if len(tok) > 1 and tok not in _STOPWORDS}


def _lexical_overlap(query: str, candidate: str) -> float:
    q = _tokenize(query)
    c = _tokenize(candidate)
    if not q or not c:
        return 0.0
    return len(q & c) / max(1, len(q))


def _norm_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _add_token_with_variants(tokens: set[str], token: str) -> None:
    normalized = _norm_text(token)
    if not normalized:
        return
    tokens.add(normalized)
    compact = _compact(normalized)
    if compact and compact != normalized:
        tokens.add(compact)


def _title_case_phrase(text: str | None) -> str:
    if not text:
        return ""
    phrase_stopwords = _STOPWORDS | {
        "article", "description", "evidence", "page", "source", "sources",
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
    }
    connectors = {"&", "and", "for", "in", "of", "on", "the", "to"}
    phrase_parts: list[str] = []
    significant = 0
    for raw in re.findall(r"[A-Za-z&'-]+", text):
        token = _norm_text(raw)
        if not token:
            continue
        is_connector = token in connectors
        if not is_connector and token in phrase_stopwords:
            continue
        if is_connector and not phrase_parts:
            continue
        phrase_parts.append(token)
        if not is_connector:
            significant += 1
        if significant >= 5:
            break

    while phrase_parts and phrase_parts[-1] in connectors:
        phrase_parts.pop()
    return _norm_text(" ".join(phrase_parts))


def _extract_fingerprint(citation: CitationGroup) -> dict[str, set[str]]:
    """
    Extract strict citation fingerprints for precision-first PDF page matching.
    """
    citation_text = citation.citation_text or ""
    exact_citation = _compact(citation_text)
    combined = " ".join(
        part
        for part in (citation.source_title or "", citation.outlet or "", citation_text)
        if part
    )

    years = set(_YEAR_RE.findall(citation_text))

    surnames: set[str] = set()
    for token in _PROPER_TOKEN_RE.findall(citation_text):
        normalized = _norm_text(token)
        if len(_compact(normalized)) < 3 or normalized in _FINGERPRINT_STOPWORDS:
            continue
        _add_token_with_variants(surnames, normalized)

    doc_numbers: set[str] = set()
    for pattern in _DOC_NUMBER_PATTERNS:
        for match in pattern.findall(citation_text):
            _add_token_with_variants(doc_numbers, match)

    acronyms = {
        token
        for token in _ACRONYM_RE.findall(citation_text)
        if not token.isdigit() and token.lower() not in _FINGERPRINT_STOPWORDS
    }

    outlet_phrase = _title_case_phrase(citation.outlet) or _title_case_phrase(citation.source_title)
    outlet_phrases: set[str] = set()
    if outlet_phrase:
        _add_token_with_variants(outlet_phrases, outlet_phrase)

    # Use source/outlet metadata to recover useful fingerprints when parsing
    # placed distinctive source words outside citation_text.
    for token in _PROPER_TOKEN_RE.findall(combined):
        normalized = _norm_text(token)
        if len(_compact(normalized)) < 3 or normalized in _FINGERPRINT_STOPWORDS:
            continue
        _add_token_with_variants(surnames, normalized)

    return {
        "exact_citation": {exact_citation} if exact_citation else set(),
        "years": years,
        "surnames": surnames,
        "doc_numbers": doc_numbers,
        "acronyms": acronyms,
        "outlet_phrase": outlet_phrases,
    }


def _token_present(page_text: str, page_compact: str, token: str) -> bool:
    if not token:
        return False
    if re.fullmatch(r"[a-z0-9]+", token):
        return bool(re.search(rf"\b{re.escape(token)}\b", page_text))
    compact = _compact(token)
    return bool(compact and compact in page_compact) or token in page_text


def _page_matches_fingerprint(page_text_full: str, page_text_upper: str, fp: dict[str, set[str]]) -> bool:
    page_text = _norm_text(page_text_full)
    page_compact = _compact(page_text_full)

    if any(exact and exact in page_compact for exact in fp.get("exact_citation", set())):
        return True

    year_match = any(year in page_text for year in fp["years"])
    surname_match = any(_token_present(page_text, page_compact, token) for token in fp["surnames"])
    doc_number_match = any(_token_present(page_text, page_compact, token) for token in fp["doc_numbers"])
    acronym_match = any(acronym in page_text_upper for acronym in fp["acronyms"])
    outlet_match = any(_token_present(page_text, page_compact, token) for token in fp["outlet_phrase"])

    strong_matches = sum([
        surname_match,
        acronym_match,
        doc_number_match,
        outlet_match,
    ])

    if fp["years"] and (fp["surnames"] or fp["acronyms"] or fp["doc_numbers"] or fp["outlet_phrase"]):
        return year_match and strong_matches >= 1

    return strong_matches >= 2


def _pick_with_llm(citation_text: str, candidates: list[PDFPageEvidence]) -> tuple[Optional[int], float]:
    """
    Ask the model to pick the best page among shortlisted candidates.
    Returns (page_number, confidence) where page_number can be None.
    """
    options = []
    for page in candidates:
        snippet = (page.text or "").replace("\n", " ")[:800]
        options.append(f"Page {page.page_number}: {snippet}")

    prompt = (
        "You are matching one citation from an HTML article to one screenshot page in a PDF. "
        "Choose the best page based on source names, titles, outlets, dates, document numbers, and distinctive phrases. "
        "Prefer exact entity/date/number alignment over broad topical similarity.\n\n"
        f"Citation:\n{citation_text[:900]}\n\n"
        "Candidate pages:\n"
        + "\n\n".join(options)
        + "\n\nReturn JSON only: {\"page_number\": <int|null>, \"confidence\": <0..1>}"
    )

    resp = _client.chat.completions.create(
        model="gpt-5.4-mini",
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    content = (resp.choices[0].message.content or "").strip()

    # Extract first JSON object if the model adds extra text.
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        return None, 0.0

    try:
        data = json.loads(match.group(0))
        page_number = data.get("page_number")
        confidence = float(data.get("confidence", 0.0))
        if page_number is None:
            return None, max(0.0, min(1.0, confidence))
        return int(page_number), max(0.0, min(1.0, confidence))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None, 0.0


def match_citations_to_pdf_pages(
    citation_groups: list[CitationGroup],
    page_evidence: list[PDFPageEvidence],
) -> dict[str, ExtractedImage]:
    """
    Returns a mapping: citation_id -> matched PDF page image.
    """
    if not citation_groups or not page_evidence:
        return {}

    page_full_texts = [
        (p.text or p.image.src).replace("\n", " ")
        for p in page_evidence
    ]
    page_upper_texts = [text.upper() for text in page_full_texts]
    page_texts = [
        text[:2000]
        for text in page_full_texts
    ]
    page_embeds = _embed(page_texts)

    citation_queries = [
        f"{c.source_title or ''} {c.outlet or ''} {c.date or ''} {c.citation_text}".strip()[:2000]
        for c in citation_groups
    ]
    cit_embeds = _embed(citation_queries)

    by_citation: dict[str, ExtractedImage] = {}
    for i, citation in enumerate(citation_groups):
        query = citation_queries[i]
        fingerprint = _extract_fingerprint(citation)
        strong_tokens = (
            fingerprint["surnames"]
            | fingerprint["acronyms"]
            | fingerprint["doc_numbers"]
            | fingerprint["outlet_phrase"]
        )
        if not fingerprint["years"] and not strong_tokens:
            continue

        filtered_indices = [
            j for j in range(len(page_evidence))
            if _page_matches_fingerprint(page_full_texts[j], page_upper_texts[j], fingerprint)
        ]
        if not filtered_indices:
            continue

        # 1) shortlist top fingerprint-compatible pages by embedding similarity
        scored: list[tuple[float, float, float, int]] = []
        for j in filtered_indices:
            cosine = _cosine(cit_embeds[i], page_embeds[j])
            overlap = _lexical_overlap(query, page_texts[j])
            combined = (0.78 * cosine) + (0.22 * overlap)
            scored.append((combined, cosine, overlap, j))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_indices = [j for _, _, _, j in scored[: min(_TOP_K, len(scored))]]
        candidates = [page_evidence[j] for j in top_indices]

        # 2) LLM final selection over shortlist
        llm_page_no, conf = _pick_with_llm(query, candidates)

        selected: Optional[PDFPageEvidence] = None
        if llm_page_no is not None and conf >= _LLM_MIN_CONFIDENCE:
            selected = next((p for p in candidates if p.page_number == llm_page_no), None)

        # 3) precision-first fallback: only accept top retrieval when clearly strong.
        if selected is None and scored:
            best_score, _, _, best_idx = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0.0
            if best_score >= _MIN_COMBINED_SCORE and (best_score - second_score) >= _MIN_SCORE_MARGIN:
                selected = page_evidence[best_idx]

        if selected is not None and _page_matches_fingerprint(
            selected.text or selected.image.src,
            (selected.text or selected.image.src).upper(),
            fingerprint,
        ):
            by_citation[citation.citation_id] = selected.image.model_copy(deep=True)

    return by_citation
