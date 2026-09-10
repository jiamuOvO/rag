from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Page:
    number: int
    text: str
    extraction_method: str
    ocr_confidence: float | None = None
    status: str = "completed"
    error_code: str | None = None
    error_message: str | None = None
    parser_version: str = "pymupdf-rapidocr-v1"


@dataclass
class Chunk:
    chunk_id: str
    paper_id: str
    paper_name: str
    page_start: int
    page_end: int
    section_path: str
    text: str
    token_count: int
    ordinal: int = 0
    overlap_tokens: int = 0
    chunk_reason: str = "structure_or_size_boundary"
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None
    parser_version: str = "pymupdf-rapidocr-v1"
    chunker_version: str = "structure-v1"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Evidence:
    evidence_id: str
    chunk_id: str
    paper_id: str
    paper_name: str
    page_start: int
    page_end: int
    section_path: str
    excerpt: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QueryResult:
    request_id: str
    answer: str
    answer_mode: str
    evidence: list[Evidence] = field(default_factory=list)
    insufficient_evidence: bool = False
    degraded: bool = False
    degradation_reason: str | None = None
    warnings: list[dict] = field(default_factory=list)
    corpus_incomplete: bool = False
    normalized_query: str | None = None
    structured_extraction: dict | None = None
    generation: dict = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        return data
