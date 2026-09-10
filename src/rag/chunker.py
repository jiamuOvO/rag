from __future__ import annotations

import hashlib

from .models import Chunk, Page
from .text import is_heading, split_paragraphs, tokenize


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

    def flush() -> None:
        nonlocal buffer, count
        if not buffer:
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
        ))
        if overlap_tokens:
            kept: list[tuple[int, str, list[str]]] = []
            kept_count = 0
            for item in reversed(buffer):
                if kept and kept_count + len(item[2]) > overlap_tokens:
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
            tokens = tokenize(paragraph)
            if not tokens:
                continue
            if len(tokens) > target_tokens:
                words = paragraph.split()
                if not words:
                    words = list(paragraph)
                step = max(1, target_tokens - overlap_tokens)
                for start in range(0, len(words), step):
                    part = " ".join(words[start : start + target_tokens])
                    part_tokens = tokenize(part)
                    if count and count + len(part_tokens) > target_tokens:
                        flush()
                    buffer.append((page.number, part, part_tokens))
                    count += len(part_tokens)
                    if count >= target_tokens:
                        flush()
                continue
            if buffer and count + len(tokens) > target_tokens:
                flush()
            buffer.append((page.number, paragraph, tokens))
            count += len(tokens)
    flush()
    for index, chunk in enumerate(chunks):
        chunk.previous_chunk_id = chunks[index - 1].chunk_id if index else None
        chunk.next_chunk_id = chunks[index + 1].chunk_id if index + 1 < len(chunks) else None
    return chunks
