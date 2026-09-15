"""Read-only PDF text-quality audit with bounded alternative extraction samples."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter

import fitz

from rag.config import Settings
from rag.parser import PdfParser
from rag.text import CONTROL_OR_ENCODING_RE, normalize_text, text_quality_issues


def compact_sample(text: str) -> str:
    match = CONTROL_OR_ENCODING_RE.search(text or "")
    if not match:
        return ""
    start, end = max(0, match.start() - 28), min(len(text), match.end() + 28)
    return text[start:end].encode("unicode_escape").decode("ascii")


def recovered_word(stored: str, alternative: str) -> str | None:
    match = CONTROL_OR_ENCODING_RE.search(stored or "")
    if not match or not alternative:
        return None
    left = re.search(r"[A-Za-z]{2,12}$", stored[:match.start()])
    right = re.match(r"[A-Za-z]{1,12}", stored[match.end():])
    if not left or not right:
        return None
    recovered = re.search(
        rf"\b{re.escape(left.group())}[A-Za-z]{{0,12}}{re.escape(right.group())}\b",
        alternative, re.IGNORECASE,
    )
    return recovered.group() if recovered else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--ocr", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    with sqlite3.connect(settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        pages = conn.execute(
            """SELECT p.paper_id,p.page_number,p.text,p.extraction_method,pa.file_name,pa.file_path
               FROM pages p JOIN papers pa ON pa.paper_id=p.paper_id ORDER BY pa.file_name,p.page_number"""
        ).fetchall()
        chunks = conn.execute("SELECT paper_id,text FROM chunks").fetchall()
    affected_pages = [row for row in pages if text_quality_issues(row["text"])]
    affected_chunks = [row for row in chunks if text_quality_issues(row["text"])]
    methods = Counter(row["extraction_method"] for row in affected_pages)
    papers = Counter(row["file_name"] for row in affected_pages)
    samples = []
    ocr_parser = PdfParser(ocr_enabled=True) if args.ocr else None
    for row in affected_pages[:max(0, args.samples)]:
        with fitz.open(row["file_path"]) as doc:
            page = doc[row["page_number"] - 1]
            plain = normalize_text(page.get_text("text"))
            blocks = normalize_text("\n".join(
                str(block[4]) for block in page.get_text("blocks") if len(block) > 4
            ))
            ocr, confidence = ocr_parser._ocr_page(page) if ocr_parser else ("", None)
        samples.append({
            "paper": row["file_name"], "page": row["page_number"],
            "stored_method": row["extraction_method"],
            "stored_issue": text_quality_issues(row["text"]),
            "plain_issue": text_quality_issues(plain),
            "blocks_issue": text_quality_issues(blocks),
            "ocr_issue": text_quality_issues(ocr) if args.ocr else None,
            "ocr_confidence": confidence,
            "ocr_recovered_word": recovered_word(row["text"], ocr) if args.ocr else None,
            "stored_sample": compact_sample(row["text"]),
            "plain_sample": compact_sample(plain),
            "blocks_sample": compact_sample(blocks),
            "ocr_sample": compact_sample(ocr) if args.ocr else None,
        })
    print(json.dumps({
        "pages": {"total": len(pages), "affected": len(affected_pages),
                  "papers": len(papers), "by_method": dict(methods),
                  "by_paper": dict(papers)},
        "chunks": {"total": len(chunks), "affected": len(affected_chunks),
                   "papers": len({row["paper_id"] for row in affected_chunks})},
        "samples": samples,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
