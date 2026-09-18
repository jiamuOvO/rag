from __future__ import annotations

import hashlib
import re

from .models import Chunk, Page
from .text import is_heading, split_paragraphs, tokenize

MAX_SAFE_CHARS = 512  # 2048 / four UTF-8 bytes per Unicode character


def _split_to_budget(text: str, budget: int) -> list[str]:
    # A regex token is not a model token: an unbroken URL, identifier, or number
    # can be one local token but thousands of BPE tokens.  One character per local
    # token is deliberately conservative (even byte-tokenized UTF-8 is < 3 tokens
    # per character), leaving ample room below qwen's 2048-token input limit.
    char_budget = min(max(budget, 64), MAX_SAFE_CHARS)
    if len(text) <= char_budget and len(tokenize(text)) <= budget:
        return [text]
    boundaries = [m.start() for m in re.finditer(r"\s+", text)]
    middle = min(boundaries, key=lambda i: abs(i - len(text) // 2)) if boundaries else len(text) // 2
    if middle <= 0 or middle >= len(text):
        middle = max(1, len(text) // 2)
    return (_split_to_budget(text[:middle], budget)
            + _split_to_budget(text[middle:], budget))


def _chunk_id(paper_id: str, page_start: int, ordinal: int, text: str) -> str:
    digest = hashlib.sha256(f"{paper_id}:{page_start}:{ordinal}:{text}".encode("utf-8")).hexdigest()
    return f"chk_{digest[:24]}"


def chunk_pages(
    pages: list[Page],
    paper_id: str,
    paper_name: str,
    *,
    target_tokens: int = 384,
    overlap_tokens: int = 64,
) -> list[Chunk]:
    if target_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("require target_tokens > overlap_tokens >= 0")
    chunks: list[Chunk] = []
    section = ""
    buffer: list[tuple[int, str, list[str]]] = []
    count = 0
    fresh = False

    def fits_budget(text: str) -> bool:
        # Part splitting enforces the tighter local budget.  At joins, permit a
        # small overlap as long as the absolute BPE-safe character ceiling holds.
        return len(text) <= MAX_SAFE_CHARS and len(tokenize(text)) <= target_tokens

    def flush() -> None:
        nonlocal buffer, count, fresh
        if not buffer or not fresh:
            return
        text = "\n\n".join(item[1] for item in buffer).strip()
        if not text:
            buffer, count = [], 0
            return
        ordinal = len(chunks)
        chunks.append(Chunk(
            chunk_id=_chunk_id(paper_id, buffer[0][0], ordinal, text),
            paper_id=paper_id,
            paper_name=paper_name,
            page_start=buffer[0][0],
            page_end=buffer[-1][0],
            section_path=section,
            text=text,
            token_count=len(tokenize(text)),
            ordinal=ordinal,
            overlap_tokens=overlap_tokens,
            chunker_version="structure-v2",
        ))
        fresh = False
        if overlap_tokens:
            kept: list[tuple[int, str, list[str]]] = []
            kept_count = 0
            for item in reversed(buffer):
                if kept_count + len(item[2]) > overlap_tokens:
                    # Keep the latest suffix that fits; retaining an earlier word
                    # made the next chunk exceed its budget and could duplicate it.
                    for match in reversed(list(re.finditer(r"\S+", item[1]))):
                        tail = item[1][match.start():]
                        tail_tokens = tokenize(tail)
                        if tail_tokens and kept_count + len(tail_tokens) <= overlap_tokens:
                            kept.insert(0, (item[0], tail, tail_tokens))
                            kept_count += len(tail_tokens)
                            break
                    break
                kept.insert(0, item)
                kept_count += len(item[2])
            buffer, count = kept, kept_count
        else:
            buffer, count = [], 0

    for page in pages:
        for paragraph in split_paragraphs(page.text):
            if is_heading(paragraph):
                if buffer:
                    flush()
                section = " ".join(paragraph.split())
            # Keep tokenless text too: punctuation or symbols are still source
            # content and must not disappear merely because the lexical index skips them.
            if not paragraph:
                continue
            for part in _split_to_budget(paragraph, target_tokens - overlap_tokens):
                if buffer and not fits_budget("\n\n".join([item[1] for item in buffer] + [part])):
                    flush()
                # Chinese retrieval bigrams can add a token across the overlap boundary.
                candidate = "\n\n".join([item[1] for item in buffer] + [part])
                heading_only = len(buffer) == 1 and buffer[0][1] == section
                if buffer and not fits_budget(candidate) and not heading_only:
                    buffer, count = [], 0
                buffer.append((page.number, part, tokenize(part)))
                count = len(tokenize("\n\n".join(item[1] for item in buffer)))
                fresh = True
                if count >= target_tokens:
                    flush()
    flush()
    for index, chunk in enumerate(chunks):
        chunk.previous_chunk_id = chunks[index - 1].chunk_id if index else None
        chunk.next_chunk_id = chunks[index + 1].chunk_id if index + 1 < len(chunks) else None
    return chunks
