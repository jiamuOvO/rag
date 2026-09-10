from __future__ import annotations

import re
import unicodedata

TOKEN_RE = re.compile(
    r"(?:\d+(?:\.\d+)?(?:°c|wt%|mol%|%|mpa|kpa|bar|h|min|s)?)|"
    r"(?:[a-z]+(?:[-_/][a-z0-9]+)*\d*(?:\[[ivx]+\])?)|"
    r"(?:[a-z]?\d+[a-z][a-z0-9]*)|"
    r"(?:[\u4e00-\u9fff])",
    re.IGNORECASE,
)


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

