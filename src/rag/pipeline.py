from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .chunker import chunk_pages
from .config import Settings
from .errors import RagError, error_info
from .extraction import EXTRACTION_SCHEMA_VERSION, extract_reaction_evidence
from .models import QueryResult
from .observability import JsonLogger
from .parser import PdfParser
from .providers import ExtractiveProvider, configured_embedding_provider, configured_provider
from .retriever import (BM25Retriever, DenseRetriever, analyze_query,
                        has_reliable_lexical_support, reciprocal_rank_fusion)
from .reranker import configured_reranker
from .store import Store


def file_hash(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def paper_id(source_hash: str) -> str:
    return f"paper_{source_hash[:24]}"


class Pipeline:
    def __init__(self, settings: Settings | None = None, *, logging_enabled: bool = True):
        self.settings = settings or Settings.load()
        self.settings.ensure_runtime_dirs()
        self.store = Store(self.settings.db_path)
        self.log = JsonLogger(self.settings.log_path, enabled=logging_enabled)
        self.parser = PdfParser(
            ocr_enabled=self.settings.ocr_enabled,
            min_page_chars=self.settings.min_page_chars,
        )
        self._retriever: BM25Retriever | None = None
        self._dense_chunks: list[dict] | None = None
        self._retriever_lock = threading.Lock()

    def _ids(self) -> tuple[str, str]:
        return f"run_{uuid.uuid4().hex}", f"req_{uuid.uuid4().hex}"

    @contextmanager
    def _stage(self, stage: str, **context):
        run_id = context.get("run_id")
        started = time.perf_counter()
        if run_id:
            versions = {"discover": "discovery-v1", "parse": "pymupdf-rapidocr-v1",
                        "chunk": "structure-v1", "index": "sqlite-index-v2"}
            self.store.start_stage(run_id, stage, paper_id=context.get("paper_id"),
                                   component_version=str(context.get("model") or versions.get(stage, "v1")))
        try:
            with self.log.stage(stage, **context) as metric:
                yield metric
        except Exception as exc:
            if run_id:
                info = error_info(exc, getattr(exc, "stage", stage), self.settings.debug).to_dict()
                self.store.finish_stage(run_id, stage, status="failed",
                                        duration_ms=round((time.perf_counter() - started) * 1000, 3),
                                        error=info)
            raise
        else:
            if run_id:
                output = next((metric[key] for key in ("chunks", "vectors", "pages", "pdfs")
                               if key in metric), None)
                self.store.finish_stage(run_id, stage, status="completed",
                                        duration_ms=round((time.perf_counter() - started) * 1000, 3),
                                        output_count=output)

    def ingest(self, *, file: Path | None = None, force: bool = False,
               run_id: str | None = None, request_id: str | None = None) -> dict:
        generated_run, generated_request = self._ids()
        run_id, request_id = run_id or generated_run, request_id or generated_request
        if self.store.run(run_id) is None:
            self.store.create_run(run_id, request_id, "ingestion")
        else:
            self.store.update_run(run_id, status="running")
        counts = {"discovered": 0, "succeeded": 0, "skipped": 0, "failed": 0,
                  "pages": 0, "ocr_pages": 0, "failed_pages": 0,
                  "chunks": 0, "embeddings": 0, "attachments": 0}
        self.log.emit("run_started", run_id=run_id, request_id=request_id, kind="ingestion")
        try:
            with self._stage("discover", run_id=run_id, request_id=request_id) as metric:
                if file:
                    candidates = [file.resolve()]
                    if not candidates[0].is_file() or candidates[0].suffix.lower() != ".pdf":
                        raise RagError("INPUT_NOT_PDF", "discover", f"not a PDF file: {file}")
                else:
                    candidates = sorted(self.settings.data_dir.glob("*.pdf"))
                attachments = sorted(self.settings.data_dir.glob("*.zip")) if not file else []
                counts["discovered"] = len(candidates)
                metric.update(pdfs=len(candidates), attachments=len(attachments))
            embedding_provider = configured_embedding_provider(self.settings)
            for path in candidates:
                source_hash = ""
                pid = None
                try:
                    source_hash = file_hash(path)
                    pid = paper_id(source_hash)
                    current = self.store.paper_by_hash(source_hash)
                    if current and current["status"] == "ready" and not force:
                        counts["skipped"] += 1
                        self.store.update_run_document(run_id, path.name, paper_id=pid,
                                                       status="skipped", stage="discover")
                        continue
                    if not current:
                        self.store.register_processing_paper(
                            paper_id=pid, source_hash=source_hash, file_name=path.name,
                            file_path=str(path), size_bytes=path.stat().st_size,
                            parser_version="pymupdf-rapidocr-v1",
                        )
                    self.store.update_run_document(run_id, path.name, paper_id=pid,
                                                   status="running", stage="parse")
                    self.store.update_run(run_id, stage="parse", counts=counts)
                    with self._stage("parse", run_id=run_id, request_id=request_id,
                                        paper_id=pid, file_name=path.name) as metric:
                        ocr_started: dict[int, float] = {}
                        def on_ocr_start(page: int) -> None:
                            ocr_started[page] = time.perf_counter()
                            self.store.start_stage(run_id, "ocr", paper_id=pid,
                                                   component_version="rapidocr-onnxruntime-1.4.4")
                        def on_ocr(page: int, success: bool, confidence: float | None) -> None:
                            duration = round((time.perf_counter() - ocr_started.pop(page, time.perf_counter())) * 1000, 3)
                            self.store.finish_stage(run_id, "ocr",
                                                    status="completed" if success else "failed",
                                                    duration_ms=max(0.0, duration),
                                                    output_count=1 if success else 0)
                            self.log.emit("ocr_page", stage="ocr", run_id=run_id,
                                          request_id=request_id, paper_id=pid, page=page,
                                          success=success, confidence=confidence)
                        parsed = self.parser.parse(path, on_ocr=on_ocr, on_ocr_start=on_ocr_start)
                        for failed_page in parsed.failed_pages:
                            self.log.emit("page_degraded", stage="ocr", run_id=run_id,
                                          request_id=request_id, paper_id=pid,
                                          page=failed_page["page"],
                                          error_code=failed_page["error_code"])
                        metric.update(pages=len(parsed.pages), ocr_pages=parsed.ocr_pages,
                                      failed_pages=len(parsed.failed_pages))
                    self.store.update_run_document(run_id, path.name, paper_id=pid,
                                                   status="running", stage="chunk")
                    with self._stage("chunk", run_id=run_id, request_id=request_id,
                                        paper_id=pid) as metric:
                        chunks = chunk_pages(
                            parsed.pages, pid, path.name,
                            target_tokens=self.settings.chunk_tokens,
                            overlap_tokens=self.settings.chunk_overlap,
                        )
                        if not chunks:
                            raise RagError("CHUNK_EMPTY", "chunk", "parser output produced zero chunks")
                        metric["chunks"] = len(chunks)
                    vectors = None
                    if embedding_provider is not None:
                        self.store.update_run_document(run_id, path.name, paper_id=pid,
                                                       status="running", stage="embedding")
                        with self._stage("embedding", run_id=run_id, request_id=request_id,
                                            paper_id=pid, model=embedding_provider.model) as metric:
                            vectors = []
                            batch_size = self.settings.embedding_batch_size
                            for start in range(0, len(chunks), batch_size):
                                vectors.extend(embedding_provider.embed(
                                    [chunk.text for chunk in chunks[start:start + batch_size]]
                                ))
                            if len(vectors) != len(chunks):
                                raise RagError("EMBEDDING_COUNT_MISMATCH", "embedding",
                                               "embedding count does not match chunk count")
                            metric.update(vectors=len(vectors), dimensions=len(vectors[0]) if vectors else 0)
                    self.store.update_run_document(run_id, path.name, paper_id=pid,
                                                   status="running", stage="index")
                    with self._stage("index", run_id=run_id, request_id=request_id,
                                        paper_id=pid) as metric:
                        self.store.replace_paper({
                            "paper_id": pid,
                            "source_hash": source_hash,
                            "file_name": path.name,
                            "file_path": str(path),
                            "size_bytes": path.stat().st_size,
                            "page_count": len(parsed.pages),
                            "ocr_pages": parsed.ocr_pages,
                            "failed_pages": parsed.failed_pages,
                            "status": "partial_failed" if parsed.failed_pages else "ready",
                            "parser_version": "pymupdf-rapidocr-v1",
                        }, chunks, vectors, pages=parsed.pages,
                            embedding_provider=embedding_provider.name if embedding_provider else None,
                            embedding_model=embedding_provider.model if embedding_provider else None)
                        metric["chunks"] = len(chunks)
                        with self._retriever_lock:
                            self._retriever = None
                            self._dense_chunks = None
                    counts["succeeded"] += 1
                    counts["pages"] += len(parsed.pages)
                    counts["ocr_pages"] += parsed.ocr_pages
                    counts["failed_pages"] += len(parsed.failed_pages)
                    counts["chunks"] += len(chunks)
                    counts["embeddings"] += len(vectors or [])
                    self.store.update_run_document(
                        run_id, path.name, paper_id=pid,
                        status="partial_failed" if parsed.failed_pages else "completed", stage="index",
                        counts={"pages": len(parsed.pages), "ocr_pages": parsed.ocr_pages,
                                "chunks": len(chunks), "embeddings": len(vectors or []),
                                "failed_pages": len(parsed.failed_pages)},
                    )
                except Exception as exc:
                    counts["failed"] += 1
                    info = error_info(exc, getattr(exc, "stage", "parse"), self.settings.debug).to_dict()
                    self.store.update_run_document(run_id, path.name, paper_id=pid,
                                                   status="failed", stage=info["stage"], error=info)
                    if pid:
                        self.store.mark_paper_failed(pid, info)
                    self.log.emit("document_failed", run_id=run_id, request_id=request_id,
                                  paper_id=pid, file_name=path.name, **info)
                finally:
                    self.store.update_run(run_id, counts=counts)
            for attachment in attachments:
                base = attachment.stem.removesuffix("_SI")
                linked = next((p for p in self.store.papers() if Path(p["file_name"]).stem == base), None)
                digest = file_hash(attachment)
                self.store.register_attachment({
                    "attachment_id": f"att_{digest[:24]}",
                    "paper_id": linked["paper_id"] if linked else None,
                    "file_name": attachment.name,
                    "file_path": str(attachment),
                    "size_bytes": attachment.stat().st_size,
                })
                counts["attachments"] += 1
            if (counts["failed"] or counts["failed_pages"]) and counts["succeeded"] + counts["skipped"]:
                status = "partial_failed"
            elif counts["failed"]:
                status = "failed"
            else:
                status = "completed"
            self.store.update_run(run_id, status=status, stage="index", counts=counts, finished=True)
            self.log.emit("run_completed", run_id=run_id, request_id=request_id,
                          status=status, counts=counts)
        except Exception as exc:
            info = error_info(exc, getattr(exc, "stage", "discover"), self.settings.debug).to_dict()
            self.store.update_run(run_id, status="failed", stage=info["stage"], counts=counts,
                                  error=info, finished=True)
            self.log.emit("run_failed", run_id=run_id, request_id=request_id, **info)
        return self.store.run(run_id) or {"run_id": run_id, "status": "failed"}

    def query(self, question: str, *, top_k: int = 8,
              paper_ids: list[str] | None = None, request_id: str | None = None) -> QueryResult:
        if not question.strip():
            raise RagError("QUERY_EMPTY", "retrieve", "question cannot be empty")
        if not 1 <= top_k <= 50:
            raise RagError("TOP_K_INVALID", "retrieve", "top_k must be between 1 and 50")
        request_id = request_id or f"req_{uuid.uuid4().hex}"
        timings: dict[str, float] = {}
        degraded_reasons: list[str] = []
        analysis = analyze_query(question)
        normalized_query = analysis["normalized"]
        warnings: list[dict] = []
        papers = self.store.papers()
        requested = set(paper_ids or [])
        missing_scope = sorted(requested - {item["paper_id"] for item in papers})
        incomplete = [item for item in papers if (item["failed_pages"] or item["status"] != "ready") and
                      (not requested or item["paper_id"] in requested)]
        corpus_incomplete = bool(missing_scope or incomplete)
        if missing_scope:
            warnings.append({"code": "PAPER_SCOPE_MISSING", "message": "部分限定论文不存在或不可用",
                             "paper_ids": missing_scope})
        if incomplete:
            warnings.append({"code": "CORPUS_PARTIAL", "message": "部分论文页面未参与检索",
                             "paper_ids": [item["paper_id"] for item in incomplete]})
            self.log.emit("corpus_partial", request_id=request_id,
                          paper_ids=[item["paper_id"] for item in incomplete],
                          affected_pages=sum(len(item["failed_pages"]) for item in incomplete))
        self.store.begin_query(request_id, question, normalized_query, paper_ids,
                               query_tokens=analysis["tokens"], expansions=analysis["expansions"])
        started = time.perf_counter()
        with self._stage("retrieve", request_id=request_id) as metric:
            if paper_ids:
                chunks = self.store.chunks(paper_ids)
                retriever = BM25Retriever(chunks)
            else:
                with self._retriever_lock:
                    if self._retriever is None:
                        self._retriever = BM25Retriever(self.store.chunks())
                    retriever = self._retriever
                chunks = retriever.chunks
            candidate_k = max(30, top_k * 4)
            lexical = retriever.search(question, candidate_k)
            dense = []
            try:
                embedding_provider = configured_embedding_provider(self.settings)
                if embedding_provider is None:
                    degraded_reasons.append("EMBEDDING_DISABLED")
                    self.log.emit("retrieval_degraded", request_id=request_id,
                                  error_code="EMBEDDING_DISABLED", exception_type="Configuration")
                else:
                    query_vector = embedding_provider.embed([question])[0]
                    if paper_ids:
                        dense_chunks = self.store.chunks_with_embeddings(
                            paper_ids, self.settings.embedding_model
                        )
                    else:
                        with self._retriever_lock:
                            if self._dense_chunks is None:
                                self._dense_chunks = self.store.chunks_with_embeddings(
                                    model=self.settings.embedding_model
                                )
                            dense_chunks = self._dense_chunks
                    if not dense_chunks:
                        raise RagError("EMBEDDING_INDEX_EMPTY", "retrieve",
                                       "no stored embeddings for configured model")
                    dense = DenseRetriever(dense_chunks).search(query_vector, candidate_k)
            except Exception as exc:
                reason = error_info(exc, getattr(exc, "stage", "retrieve"), self.settings.debug).error_code
                degraded_reasons.append(reason)
                self.log.emit("retrieval_degraded", request_id=request_id, error_code=reason,
                              exception_type=type(exc).__name__)
            evidence = reciprocal_rank_fusion(lexical, dense, top_k=top_k) if dense else lexical[:top_k]
            fused = list(evidence)
            try:
                reranker = configured_reranker(self.settings.reranker_provider)
                if reranker is not None:
                    evidence = reranker.rerank(question, evidence)[:top_k]
            except Exception as exc:
                reason = error_info(exc, "rerank", self.settings.debug).error_code
                degraded_reasons.append(reason)
                self.log.emit("reranker_degraded", request_id=request_id, error_code=reason,
                              exception_type=type(exc).__name__)
            evidence = [item for item in evidence if has_reliable_lexical_support(question, item.excerpt)]
            selected_ids = {item.chunk_id for item in evidence[:5]}
            self.store.save_query_candidates(request_id, "bm25", lexical, selected_ids)
            if dense:
                self.store.save_query_candidates(request_id, "dense", dense, selected_ids)
                self.store.save_query_candidates(request_id, "rrf", fused, selected_ids)
            if self.settings.reranker_provider not in {"", "disabled", "none"}:
                self.store.save_query_candidates(request_id, "pre_rerank", fused, selected_ids)
                self.store.save_query_candidates(request_id, "rerank", evidence, selected_ids)
            metric.update(candidates=len(chunks), lexical=len(lexical), dense=len(dense),
                          returned=len(evidence), degraded=bool(degraded_reasons))
        timings["retrieve"] = round((time.perf_counter() - started) * 1000, 3)
        # A hit must contain at least one lexical term. BM25 returns only positive hits.
        if not evidence:
            result = QueryResult(
                request_id=request_id,
                answer="当前语料中证据不足，无法可靠回答该问题。",
                answer_mode="refusal",
                evidence=[],
                insufficient_evidence=True,
                degraded=bool(degraded_reasons),
                degradation_reason=",".join(degraded_reasons) or None,
                warnings=warnings, corpus_incomplete=corpus_incomplete,
                normalized_query=normalized_query,
                timings_ms=timings,
            )
            self.store.finish_query(request_id, status="refused", answer_mode="refusal",
                                    degraded=result.degraded, insufficient_evidence=True,
                                    corpus_incomplete=corpus_incomplete, warnings=warnings, timings=timings,
                                    generation_provider="none", prompt_version="refusal-v1",
                                    answer_text=result.answer)
            return result
        selected = evidence[:5]
        generate_started = time.perf_counter()
        degraded, reason = bool(degraded_reasons), None
        with self._stage("generate", request_id=request_id) as metric:
            try:
                provider = configured_provider(self.settings)
                answer = provider.answer(question, selected)
                if provider.name == "extractive_demo":
                    degraded = True
                    degraded_reasons.append("CHAT_NOT_CONFIGURED")
                    self.log.emit("generation_degraded", request_id=request_id,
                                  error_code="CHAT_NOT_CONFIGURED", exception_type="Configuration")
                else:
                    cited = set(re.findall(r"\[(ev_[A-Za-z0-9_-]+)\]", answer))
                    allowed = {item.evidence_id for item in selected}
                    if not cited or not cited.issubset(allowed):
                        raise RagError("CITATION_BINDING_INVALID", "generate",
                                       "model answer cited missing or unknown evidence IDs")
            except Exception as exc:
                degraded = True
                reason = error_info(exc, "generate", self.settings.debug).error_code
                degraded_reasons.append(reason)
                self.log.emit("generation_degraded", request_id=request_id,
                              error_code=reason, exception_type=type(exc).__name__)
                provider = ExtractiveProvider()
                answer = provider.answer(question, selected)
            metric.update(provider=provider.name, evidence=len(selected), degraded=degraded)
        timings["generate"] = round((time.perf_counter() - generate_started) * 1000, 3)
        timings["total"] = round((time.perf_counter() - started) * 1000, 3)
        if degraded_reasons:
            warnings.append({"code": "COMPONENT_DEGRADED",
                             "message": "部分组件不可用，本次结果已显式降级",
                             "reasons": degraded_reasons})
        structured = None
        if self.settings.structured_extraction_enabled:
            try:
                structured = extract_reaction_evidence(selected)
                extraction_run_id = f"ext_{uuid.uuid4().hex}"
                self.store.save_extraction(extraction_run_id, request_id, structured,
                                           extractor_version="rules-v1")
                structured["extraction_run_id"] = extraction_run_id
            except Exception as exc:
                code = error_info(exc, "extract", self.settings.debug).error_code
                degraded = True
                warnings.append({"code": code, "message": "结构化抽取失败，基础问答不受影响"})
                self.log.emit("structured_extraction_failed", request_id=request_id,
                              error_code=code, exception_type=type(exc).__name__)
        result = QueryResult(
            request_id=request_id, answer=answer, answer_mode=provider.name,
            evidence=selected, degraded=degraded,
            degradation_reason=",".join(degraded_reasons) or reason,
            warnings=warnings, corpus_incomplete=corpus_incomplete,
            normalized_query=normalized_query,
            structured_extraction=structured,
            generation={"provider": provider.name,
                        "model": getattr(provider, "model", None),
                        "configured_model": self.settings.chat_model,
                        "prompt_version": getattr(provider, "prompt_version", None)},
            timings_ms=timings,
        )
        self.store.finish_query(request_id, status="completed", answer_mode=provider.name,
                                degraded=degraded, insufficient_evidence=False,
                                corpus_incomplete=corpus_incomplete, warnings=warnings, timings=timings,
                                generation_provider=provider.name,
                                generation_model=getattr(provider, "model", None) or self.settings.chat_model,
                                prompt_version=getattr(provider, "prompt_version", None),
                                answer_text=answer)
        return result

    def rebuild_embeddings(self) -> dict:
        run_id, request_id = self._ids()
        self.store.create_run(run_id, request_id, "embedding_rebuild")
        counts = {"chunks": 0, "embeddings": 0, "dimensions": 0}
        try:
            provider = configured_embedding_provider(self.settings)
            if provider is None:
                raise RagError("EMBEDDING_CONFIG_MISSING", "embedding",
                               "embedding provider is disabled or not configured")
            chunks = self.store.chunks()
            if not chunks:
                raise RagError("CHUNK_INDEX_EMPTY", "embedding", "no chunks available to embed")
            counts["chunks"] = len(chunks)
            with self._stage("embedding", run_id=run_id, request_id=request_id,
                             model=provider.model) as metric:
                vectors = []
                for start in range(0, len(chunks), self.settings.embedding_batch_size):
                    vectors.extend(provider.embed([item["text"] for item in
                                                   chunks[start:start + self.settings.embedding_batch_size]]))
                if len(vectors) != len(chunks):
                    raise RagError("EMBEDDING_COUNT_MISMATCH", "embedding",
                                   "embedding count does not match chunk count")
                counts.update(embeddings=len(vectors), dimensions=len(vectors[0]))
                metric.update(vectors=len(vectors), dimensions=len(vectors[0]))
            with self._stage("index", run_id=run_id, request_id=request_id,
                             model=f"{provider.name}:{provider.model}") as metric:
                self.store.replace_all_embeddings(chunks, vectors, provider=provider.name, model=provider.model)
                metric["vectors"] = len(vectors)
                with self._retriever_lock:
                    self._dense_chunks = None
            self.store.update_run(run_id, status="completed", stage="index", counts=counts, finished=True)
        except Exception as exc:
            info = error_info(exc, getattr(exc, "stage", "embedding"), self.settings.debug).to_dict()
            self.store.update_run(run_id, status="failed", stage=info["stage"], counts=counts,
                                  error=info, finished=True)
        return self.store.run(run_id) or {"run_id": run_id, "status": "failed"}

    def doctor(self) -> dict:
        deps = self.parser.dependencies()
        db_ok = False
        try:
            db_ok = self.store.health()
        except Exception:
            pass
        return {
            "python_supported": __import__("sys").version_info >= (3, 11),
            "dependencies": deps,
            "data_dir": str(self.settings.data_dir),
            "data_dir_exists": self.settings.data_dir.is_dir(),
            "pdf_count": len(list(self.settings.data_dir.glob("*.pdf"))) if self.settings.data_dir.is_dir() else 0,
            "database": str(self.settings.db_path),
            "database_ok": db_ok,
            "ocr_enabled": self.settings.ocr_enabled,
            "chat_provider": self.settings.chat_provider,
            "chat_configured": self.settings.chat_provider == "extractive" or all([
                self.settings.chat_base_url, self.settings.chat_api_key, self.settings.chat_model
            ]),
            "embedding_provider": self.settings.embedding_provider,
            "embedding_model": self.settings.embedding_model,
            "embedding_configured": self.settings.embedding_provider in {"", "disabled", "none"} or all([
                self.settings.embedding_base_url, self.settings.embedding_api_key, self.settings.embedding_model
            ]),
            "embedding_index": self.store.embedding_stats(),
            "reranker_provider": self.settings.reranker_provider,
            "structured_extraction_enabled": self.settings.structured_extraction_enabled,
        }
