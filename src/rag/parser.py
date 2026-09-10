from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import RagError
from .models import Page
from .text import normalize_text


@dataclass
class ParseResult:
    pages: list[Page]
    ocr_pages: int
    failed_pages: list[dict]


class PdfParser:
    def __init__(self, *, ocr_enabled: bool = True, min_page_chars: int = 80):
        self.ocr_enabled = ocr_enabled
        self.min_page_chars = min_page_chars
        self._ocr = None

    @staticmethod
    def dependencies() -> dict[str, bool]:
        try:
            import fitz  # noqa: F401
            fitz_ok = True
        except ImportError:
            fitz_ok = False
        try:
            import rapidocr_onnxruntime  # noqa: F401
            ocr_ok = True
        except ImportError:
            ocr_ok = False
        return {"pymupdf": fitz_ok, "rapidocr": ocr_ok}

    def _engine(self):
        if self._ocr is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise RagError("OCR_DEPENDENCY_MISSING", "ocr", "RapidOCR is not installed") from exc
            self._ocr = RapidOCR()
        return self._ocr

    def _ocr_page(self, page) -> tuple[str, float | None]:
        pix = page.get_pixmap(dpi=200, alpha=False)
        result, _ = self._engine()(pix.tobytes("png"))
        if not result:
            return "", None
        lines, scores = [], []
        for item in result:
            if len(item) >= 3:
                lines.append(str(item[1]))
                scores.append(float(item[2]))
        confidence = sum(scores) / len(scores) if scores else None
        return normalize_text("\n".join(lines)), confidence

    def parse(
        self,
        path: Path,
        *,
        page_limit: int | None = None,
        on_ocr: Callable[[int, bool, float | None], None] | None = None,
        on_ocr_start: Callable[[int], None] | None = None,
    ) -> ParseResult:
        try:
            import fitz
        except ImportError as exc:
            raise RagError("PDF_DEPENDENCY_MISSING", "parse", "PyMuPDF is not installed") from exc
        pages: list[Page] = []
        failed: list[dict] = []
        ocr_count = 0
        try:
            doc = fitz.open(path)
        except Exception as exc:
            raise RagError("PDF_OPEN_FAILED", "parse", f"cannot open PDF: {path.name}") from exc
        with doc:
            total = min(len(doc), page_limit) if page_limit else len(doc)
            for index in range(total):
                page = doc[index]
                text = normalize_text(page.get_text("text"))
                method, confidence = "text", None
                status, page_error, page_message = "completed", None, None
                if len(text) < self.min_page_chars:
                    if not self.ocr_enabled:
                        status, page_error = "partial_failed", "OCR_DISABLED"
                        failed.append({"page": index + 1, "error_code": page_error})
                    else:
                        try:
                            if on_ocr_start:
                                on_ocr_start(index + 1)
                            text, confidence = self._ocr_page(page)
                            method = "ocr"
                            ocr_count += 1
                            if not text:
                                status, page_error = "failed", "OCR_NO_TEXT"
                                failed.append({"page": index + 1, "error_code": page_error})
                            if on_ocr:
                                on_ocr(index + 1, bool(text), confidence)
                        except Exception as exc:
                            status, page_error, page_message = "failed", "OCR_PAGE_FAILED", str(exc)[:500]
                            failed.append({
                                "page": index + 1,
                                "error_code": page_error,
                                "message": page_message,
                            })
                            if on_ocr:
                                on_ocr(index + 1, False, None)
                pages.append(Page(index + 1, text, method, confidence, status,
                                  page_error, page_message))
        if not any(page.text for page in pages):
            raise RagError("PDF_NO_TEXT", "parse", f"no usable text extracted from {path.name}")
        return ParseResult(pages=pages, ocr_pages=ocr_count, failed_pages=failed)
