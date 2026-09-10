"""Bounded OCR smoke test: parse only the first two pages of one historical scan."""
from pathlib import Path

from rag.config import Settings
from rag.parser import PdfParser

settings = Settings.load()
source = settings.data_dir / "1922_Commercial_Furfural_scanned.pdf"
events = []
result = PdfParser(ocr_enabled=True, min_page_chars=80).parse(
    source, page_limit=2,
    on_ocr=lambda page, success, confidence: events.append({
        "page": page, "success": success, "confidence": confidence,
    }),
)
print({
    "source": str(source), "pages": len(result.pages), "ocr_pages": result.ocr_pages,
    "failed_pages": result.failed_pages, "ocr_events": events,
    "non_empty": all(bool(page.text.strip()) for page in result.pages),
})

