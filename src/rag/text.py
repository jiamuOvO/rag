from __future__ import annotations

import re
import unicodedata

# Units are deliberately NOT glued onto the number.  A PDF writes "71.1 %" while a user types
# "71.1%", so gluing produced the query token '71.1%' and the document token '71.1' — the query's
# most discriminative literal matched nothing.  Splitting is symmetric: alphabetic units (h, min,
# wt, mol, mpa, kpa, bar) still come from the [a-z]+ branch, and a bare '%' is dropped as noise.
# Measured effect: the gold chunk for "71.1% furfural yield at 130 C" moved from BM25 rank 7 to 1.
TOKEN_RE = re.compile(
    r"(?:\d+(?:\.\d+)?)|"
    r"(?:[a-z]+(?:[-_/][a-z0-9]+)*\d*(?:\[[ivx]+\])?)|"
    r"(?:[a-z]?\d+[a-z][a-z0-9]*)|"
    r"(?:[\u4e00-\u9fff])",
    re.IGNORECASE,
)

CONTROL_OR_ENCODING_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufffd\ue000-\uf8ff]")
HIGHLIGHT_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in",
    "is", "it", "of", "on", "or", "reported", "the", "to", "under", "was", "were", "what", "which",
}


def text_quality_issues(text: str) -> list[str]:
    """Detect corruption without pretending that lossy deletion repaired it."""
    issues = []
    if CONTROL_OR_ENCODING_RE.search(text or ""):
        issues.append("CONTROL_OR_ENCODING_POLLUTION")
    return issues


def evidence_highlights(text: str, query_tokens: list[str], expansions: list[str]) -> dict:
    """Return safe character offsets for sentences that contain actual retrieval terms."""
    terms = []
    lowered = text.casefold()
    for value in [*query_tokens, *expansions]:
        term = str(value).strip().casefold()
        if len(term) > 1 and term not in HIGHLIGHT_STOPWORDS and term not in terms and term in lowered:
            terms.append(term)
    spans: list[dict] = []
    # A decimal point is not a boundary because it is not followed by whitespace;
    # single PDF line wraps are kept inside the same sentence.
    for match in re.finditer(r".+?(?:[.!?。！？]+(?=\s|$)|(?:\r?\n){2,}|$)", text, re.DOTALL):
        sentence = match.group(0)
        sentence_lower = sentence.casefold()
        hits = [term for term in terms if term in sentence_lower]
        if hits:
            spans.append({"start": match.start(), "end": match.end(), "terms": hits})
        if len(spans) >= 6:
            break
    return {"highlight_terms": terms[:16], "highlight_spans": spans,
            "highlight_kind": "retrieval_sentence" if spans else "none"}


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "").replace("\x00", " ")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text).lower()
    raw = TOKEN_RE.findall(normalized)
    result: list[str] = []
    chinese: list[str] = []
    for token in raw:
        if "\u4e00" <= token <= "\u9fff":
            chinese.append(token)
            result.append(token)
        else:
            result.append(token)
    result.extend(a + b for a, b in zip(chinese, chinese[1:]))
    return result


def split_paragraphs(text: str) -> list[str]:
    blocks = [normalize_text(x) for x in re.split(r"\n\s*\n|(?<=[.!?])\s*\n", text)]
    return [x for x in blocks if x]


def is_heading(text: str) -> bool:
    line = " ".join(text.split())
    if not line or len(line) > 140:
        return False
    if re.match(r"^\d+(?:\.\d+)*\s+[A-Z]", line):
        return True
    return line.isupper() and 2 <= len(line.split()) <= 14
