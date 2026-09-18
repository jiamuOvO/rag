from __future__ import annotations

import hashlib
import re
import shutil
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
from .visualizations import extract_visualization
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


def validate_citation_boundary(answer: str, evidence_ids: set[str], policy: str) -> tuple[bool, str]:
    cited = set(re.findall(r"\[(ev_[A-Za-z0-9_-]+)\]", answer or ""))
    if cited - evidence_ids:
        return False, "unknown_evidence_id"
    if policy == "general":
        return (not cited and "未经当前知识库验证" in answer), "general_must_be_unverified_and_uncited"
    if not evidence_ids:
        return (not cited and "未经当前知识库验证" in answer), "no_evidence_boundary_missing"
    if not cited:
        return False, "citation_missing"
    marker = "未经当前知识库验证"
    if policy == "evidence_first" and marker in answer and re.search(r"\[ev_[A-Za-z0-9_-]+\]", answer.split(marker, 1)[1]):
        return False, "general_section_cited"
    return True, "valid"


class Pipeline:
    # `fused` and `evidence` are two independent gates: `fused` caps the RRF list, `evidence`
    # decides how many of those reach the model.  RRF gives a candidate that only one retriever
    # found at most 1/(60+1), while a candidate both found scores 0.02-0.03, so the head of the
    # fused list is a block of dual-list hits.  A window narrower than that block can never reach
    # a single-list candidate, however strong it is elsewhere.  Measured on the corpus:
    # "Which experiment reported 71.1% furfural yield at 130 C?" has 7 dual-list hits ahead of the
    # gold chunk, so `standard` needs evidence >= 8 to keep the best lexical-only hit visible.
    DEPTHS = {
        "fast": {"bm25": 16, "dense": 16, "fused": 10, "evidence": 4, "rerank": False, "context": 4},
        "standard": {"bm25": 30, "dense": 30, "fused": 16, "evidence": 8, "rerank": True, "context": 8},
        "deep": {"bm25": 60, "dense": 60, "fused": 30, "evidence": 8, "rerank": True, "context": 8},
    }
    POLICIES = {"strict", "evidence_first", "general"}
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

    def _rerank_enabled(self, depth: dict) -> bool:
        """Single source of truth for "does the reranker run at this depth".

        The refusal path used to report `bool(depth["rerank"])` while the normal path also checked
        the configured provider, so a disabled reranker was logged as active on refusals.
        """
        return bool(depth["rerank"] and self.settings.reranker_provider not in {"", "disabled", "none"})

    @contextmanager
    def _stage(self, stage: str, **context):
        run_id = context.get("run_id")
        started = time.perf_counter()
        if run_id:
            versions = {"discover": "discovery-v1", "parse": "pymupdf-rapidocr-v1",
                        "chunk": "structure-v2", "index": "sqlite-index-v2"}
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
               run_id: str | None = None, request_id: str | None = None,
               document_scope: dict | None = None) -> dict:
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
                        if document_scope:
                            self.store.link_document(
                                **document_scope, paper_id=current["paper_id"],
                                source_name=path.name, stored_path=str(path), status="ready",
                            )
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
                    if document_scope:
                        self.store.link_document(
                            **document_scope, paper_id=pid, source_name=path.name,
                            stored_path=str(path), status="processing",
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
                            embedding_model=embedding_provider.model if embedding_provider else None,
                            document_scope=document_scope)
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
                    if document_scope:
                        self.store.set_document_status(document_scope["document_id"], "failed")
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

    def retrieve(self, question: str, *, top_k: int = 8, tenant_id: str,
                 subject: str, collection_ids: list[str] | None = None,
                 conversation_id: str | None = None,
                 include_official: bool = True,
                 paper_ids: list[str] | None = None,
                 request_id: str | None = None) -> dict:
        if not question.strip():
            raise RagError("QUERY_EMPTY", "retrieve", "question cannot be empty")
        request_id = request_id or f"req_{uuid.uuid4().hex}"
        analysis = analyze_query(question)
        self.store.begin_query(request_id, question, analysis["normalized"], None,
                               query_tokens=analysis["tokens"], expansions=analysis["expansions"],
                               tenant_id=tenant_id, subject=subject, collection_ids=collection_ids,
                               conversation_id=conversation_id, include_official=include_official,
                               retrieval_question=question)
        started = time.perf_counter()
        pending = self.store.pending_scope_documents(
            tenant_id, subject, collection_ids=collection_ids,
            conversation_id=conversation_id, include_official=include_official,
        )
        chunks = self.store.scoped_chunks(
            tenant_id, subject, collection_ids=collection_ids,
            conversation_id=conversation_id, include_official=include_official, paper_ids=paper_ids,
        )
        candidate_k = max(30, top_k * 4)
        lexical = BM25Retriever(chunks).search(question, candidate_k)
        dense, degraded_reasons = [], []
        try:
            provider = configured_embedding_provider(self.settings)
            if provider is None:
                degraded_reasons.append("EMBEDDING_DISABLED")
            else:
                dense_chunks = self.store.scoped_chunks(
                    tenant_id, subject, collection_ids=collection_ids,
                    conversation_id=conversation_id, include_official=include_official,
                    paper_ids=paper_ids,
                    with_embeddings=True, embedding_model=self.settings.embedding_model,
                )
                if dense_chunks:
                    dense = DenseRetriever(dense_chunks).search(provider.embed([question])[0], candidate_k)
                else:
                    degraded_reasons.append("EMBEDDING_INDEX_EMPTY")
        except Exception as exc:
            degraded_reasons.append(error_info(exc, "retrieve", self.settings.debug).error_code)
        evidence = reciprocal_rank_fusion(lexical, dense, top_k=top_k) if dense else lexical[:top_k]
        fused = list(evidence)
        try:
            reranker = configured_reranker(self.settings.reranker_provider)
            if reranker is not None:
                evidence = reranker.rerank(question, evidence)[:top_k]
        except Exception as exc:
            degraded_reasons.append(error_info(exc, "rerank", self.settings.debug).error_code)
        evidence = [item for item in evidence if has_reliable_lexical_support(question, item.excerpt)]
        selected_ids = {item.chunk_id for item in evidence}
        self.store.save_query_candidates(request_id, "bm25", lexical, selected_ids)
        if dense:
            self.store.save_query_candidates(request_id, "dense", dense, selected_ids)
            self.store.save_query_candidates(request_id, "rrf", fused, selected_ids)
        timings = {"retrieve": round((time.perf_counter() - started) * 1000, 3)}
        warnings = ([{"code": "DOCUMENTS_NOT_READY",
                      "message": "部分所选文档尚未就绪，未参与本次检索", "documents": pending}]
                    if pending else [])
        self.store.finish_query(
            request_id, status="completed", answer_mode="retrieve_only",
            degraded=bool(degraded_reasons), insufficient_evidence=not evidence,
            corpus_incomplete=bool(pending), warnings=warnings, timings=timings,
            generation_provider="none", prompt_version="retrieve-v1", answer_text=None,
        )
        return {"request_id": request_id, "results": [item.to_dict() for item in evidence],
                "warnings": warnings, "degraded": bool(degraded_reasons),
                "degradation_reason": ",".join(dict.fromkeys(degraded_reasons)) or None,
                "timings": timings}

    def query(self, question: str, *, top_k: int = 8,
              paper_ids: list[str] | None = None, request_id: str | None = None,
              tenant_id: str | None = None, subject: str | None = None,
              collection_ids: list[str] | None = None,
              conversation_id: str | None = None,
              include_official: bool = True,
              retrieval_question: str | None = None,
              retrieval_depth: str = "standard",
              answer_policy: str = "evidence_first") -> QueryResult:
        if not question.strip():
            raise RagError("QUERY_EMPTY", "retrieve", "question cannot be empty")
        if not 1 <= top_k <= 50:
            raise RagError("TOP_K_INVALID", "retrieve", "top_k must be between 1 and 50")
        if retrieval_depth not in self.DEPTHS:
            raise RagError("RETRIEVAL_DEPTH_INVALID", "retrieve", "unknown retrieval depth")
        if answer_policy not in self.POLICIES:
            raise RagError("ANSWER_POLICY_INVALID", "generate", "unknown answer policy")
        query_started = time.perf_counter()
        normalize_started = time.perf_counter()
        depth = self.DEPTHS[retrieval_depth]
        request_id = request_id or f"req_{uuid.uuid4().hex}"
        timings: dict[str, float] = {}
        degraded_reasons: list[str] = []
        search_question = retrieval_question or question
        analysis = analyze_query(search_question)
        normalized_query = analysis["normalized"]
        timings["normalize"] = round((time.perf_counter() - normalize_started) * 1000, 3)
        timings["rewrite"] = 0.0  # retrieval_question is caller-supplied; no model rewrite stage yet
        warnings: list[dict] = []
        scoped = tenant_id is not None and subject is not None
        scoped_chunks: list[dict] | None = None
        if scoped:
            scoped_chunks = self.store.scoped_chunks(
                tenant_id, subject, collection_ids=collection_ids,
                conversation_id=conversation_id, include_official=include_official,
                paper_ids=paper_ids,
            )
            accessible_paper_ids = {item["paper_id"] for item in scoped_chunks}
            papers = [item for pid in accessible_paper_ids if (item := self.store.paper(pid))]
        else:
            papers = self.store.papers()
        if scoped:
            pending = self.store.pending_scope_documents(
                tenant_id, subject, collection_ids=collection_ids,
                conversation_id=conversation_id, include_official=include_official,
            )
            if pending:
                warnings.append({"code": "DOCUMENTS_NOT_READY",
                                 "message": "部分所选文档尚未就绪，未参与本次检索",
                                 "documents": pending})
        requested = set(paper_ids or [])
        missing_scope = sorted(requested - {item["paper_id"] for item in papers})
        incomplete = [item for item in papers if (item["failed_pages"] or item["status"] != "ready") and
                      (not requested or item["paper_id"] in requested)]
        corpus_incomplete = bool(missing_scope or incomplete or (scoped and pending))
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
                               query_tokens=analysis["tokens"], expansions=analysis["expansions"],
                               tenant_id=tenant_id, subject=subject, collection_ids=collection_ids,
                               conversation_id=conversation_id, include_official=include_official,
                               retrieval_question=search_question, retrieval_depth=retrieval_depth,
                               answer_policy=answer_policy)
        started = time.perf_counter()
        embedding_meta = {"provider": self.settings.embedding_provider,
                          "model": self.settings.embedding_model}
        embedding_provider = None
        with self._stage("retrieve", request_id=request_id) as metric:
            if scoped:
                chunks = scoped_chunks or []
                retriever = BM25Retriever(chunks)
            elif paper_ids:
                chunks = self.store.chunks(paper_ids)
                retriever = BM25Retriever(chunks)
            else:
                with self._retriever_lock:
                    if self._retriever is None:
                        self._retriever = BM25Retriever(self.store.chunks())
                    retriever = self._retriever
                chunks = retriever.chunks
            source_participation: dict[str, dict[str, int]] = {}
            for chunk in chunks:
                source = chunk.get("scope_type") or chunk.get("source_type") or "official"
                source_participation.setdefault(source, {"documents": 0, "chunks": 0})["chunks"] += 1
            for source, bucket in source_participation.items():
                bucket["documents"] = len({chunk.get("document_id") or chunk.get("paper_id")
                                            for chunk in chunks
                                            if (chunk.get("scope_type") or chunk.get("source_type") or "official") == source})
            lexical = retriever.search(search_question, max(depth["bm25"], top_k))
            dense = []
            try:
                embedding_provider = configured_embedding_provider(self.settings)
                if embedding_provider is None:
                    degraded_reasons.append("EMBEDDING_DISABLED")
                    self.log.emit("retrieval_degraded", request_id=request_id,
                                  error_code="EMBEDDING_DISABLED", exception_type="Configuration")
                else:
                    query_vector = embedding_provider.embed([search_question])[0]
                    embedding_meta.update({"provider": embedding_provider.name,
                                           "model": embedding_provider.model,
                                           **getattr(embedding_provider, "last_call", {})})
                    if scoped:
                        dense_chunks = self.store.scoped_chunks(
                            tenant_id, subject, collection_ids=collection_ids,
                            conversation_id=conversation_id, include_official=include_official,
                            paper_ids=paper_ids,
                            with_embeddings=True, embedding_model=self.settings.embedding_model,
                        )
                    elif paper_ids:
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
                    dense = DenseRetriever(dense_chunks).search(query_vector, max(depth["dense"], top_k))
            except Exception as exc:
                if embedding_provider is not None:
                    embedding_meta.update({"provider": embedding_provider.name,
                                           "model": embedding_provider.model,
                                           **getattr(embedding_provider, "last_call", {})})
                reason = error_info(exc, getattr(exc, "stage", "retrieve"), self.settings.debug).error_code
                degraded_reasons.append(reason)
                self.log.emit("retrieval_degraded", request_id=request_id, error_code=reason,
                              exception_type=type(exc).__name__)
            fusion_k = min(max(top_k, depth["fused"]), 50)
            evidence = reciprocal_rank_fusion(lexical, dense, top_k=fusion_k) if dense else lexical[:fusion_k]
            fused = list(evidence)
            rerank_started = time.perf_counter()
            try:
                reranker = configured_reranker(self.settings.reranker_provider)
                if reranker is not None and depth["rerank"]:
                    evidence = reranker.rerank(search_question, evidence)[:top_k]
            except Exception as exc:
                reason = error_info(exc, "rerank", self.settings.debug).error_code
                degraded_reasons.append(reason)
                self.log.emit("reranker_degraded", request_id=request_id, error_code=reason,
                              exception_type=type(exc).__name__)
            timings["rerank"] = round((time.perf_counter() - rerank_started) * 1000, 3)
            evidence = [item for item in evidence if has_reliable_lexical_support(search_question, item.excerpt)]
            selected_ids = {item.chunk_id for item in evidence[:depth["evidence"]]}
            self.store.save_query_candidates(request_id, "bm25", lexical, selected_ids)
            if dense:
                self.store.save_query_candidates(request_id, "dense", dense, selected_ids)
                self.store.save_query_candidates(request_id, "rrf", fused, selected_ids)
            if self._rerank_enabled(depth):
                self.store.save_query_candidates(request_id, "pre_rerank", fused, selected_ids)
                self.store.save_query_candidates(request_id, "rerank", evidence, selected_ids)
            metric.update(candidates=len(chunks), lexical=len(lexical), dense=len(dense),
                          returned=len(evidence), degraded=bool(degraded_reasons))
        timings["retrieve"] = round((time.perf_counter() - started) * 1000, 3)
        # A hit must contain at least one lexical term. BM25 returns only positive hits.
        # The local extractive fallback has no independent knowledge to offer.  With
        # no selected evidence it must remain an explicit refusal even when the
        # requested policy would allow a configured generative model to add a
        # clearly-labelled general-knowledge section.
        if not evidence and (answer_policy == "strict" or self.settings.chat_provider == "extractive"):
            timings["generate"] = 0.0
            timings["total"] = round((time.perf_counter() - query_started) * 1000, 3)
            refusal_metrics = {"bm25_candidates": len(lexical), "dense_candidates": len(dense),
                               "fused_candidates": len(fused), "final_evidence": 0,
                               "rerank": self._rerank_enabled(depth), "max_context_evidence": depth["context"],
                               "source_participation": source_participation,
                               "embedding": embedding_meta}
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
                retrieval_depth=retrieval_depth, answer_policy=answer_policy,
                retrieval_metrics=refusal_metrics,
            )
            self.store.finish_query(request_id, status="refused", answer_mode="refusal",
                                    degraded=result.degraded, insufficient_evidence=True,
                                    corpus_incomplete=corpus_incomplete, warnings=warnings, timings=timings,
                                    generation_provider="none", prompt_version="refusal-v1",
                                    answer_text=result.answer, retrieval_metrics=refusal_metrics,
                                    citation_validation="not_applicable")
            return result
        selected = evidence[:depth["evidence"]]
        generate_started = time.perf_counter()
        degraded, reason = bool(degraded_reasons), None
        attempted_provider = None
        generation_meta = {"provider": self.settings.chat_provider,
                           "model": self.settings.chat_model,
                           "configured_model": self.settings.chat_model,
                           "answer_provider": None}
        with self._stage("generate", request_id=request_id) as metric:
            try:
                provider = configured_provider(self.settings)
                attempted_provider = provider
                provider_evidence = [] if answer_policy == "general" else selected
                try:
                    answer = provider.answer(question, provider_evidence, answer_policy=answer_policy)
                except TypeError:
                    answer = provider.answer(question, provider_evidence)
                if provider.name == "extractive_demo":
                    degraded = True
                    degraded_reasons.append("CHAT_NOT_CONFIGURED")
                    self.log.emit("generation_degraded", request_id=request_id,
                                  error_code="CHAT_NOT_CONFIGURED", exception_type="Configuration")
                else:
                    allowed = {item.evidence_id for item in selected}
                    valid, citation_state = validate_citation_boundary(answer, allowed, answer_policy)
                    if not valid:
                        if hasattr(provider, "repair_citations") and selected and answer_policy != "general":
                            repaired = provider.repair_citations(question, selected, answer)
                        else:
                            try:
                                repaired = provider.answer(question, provider_evidence, answer_policy=answer_policy)
                            except TypeError:
                                repaired = provider.answer(question, provider_evidence)
                        valid, citation_state = validate_citation_boundary(repaired, allowed, answer_policy)
                        if not valid:
                            raise RagError("CITATION_BINDING_INVALID", "generate",
                                           f"model answer remained invalid after one repair: {citation_state}")
                        answer = repaired
                        warnings.append({"code": "CITATION_FORMAT_REPAIRED", "message": "模型回答经一次受约束修复后通过引用校验"})
                generation_meta.update({"provider": provider.name,
                                        "model": getattr(provider, "model", None),
                                        "prompt_version": getattr(provider, "prompt_version", None),
                                        "answer_provider": provider.name,
                                        **getattr(provider, "last_call", {})})
            except Exception as exc:
                degraded = True
                reason = error_info(exc, "generate", self.settings.debug).error_code
                degraded_reasons.append(reason)
                self.log.emit("generation_degraded", request_id=request_id,
                              error_code=reason, exception_type=type(exc).__name__)
                if attempted_provider is not None:
                    generation_meta.update({"provider": attempted_provider.name,
                                            "model": getattr(attempted_provider, "model", None),
                                            "prompt_version": getattr(attempted_provider, "prompt_version", None),
                                            **getattr(attempted_provider, "last_call", {})})
                provider = ExtractiveProvider()
                answer = provider.answer(question, selected)
                generation_meta["answer_provider"] = provider.name
            metric.update(provider=provider.name, evidence=len(selected), degraded=degraded)
        timings["generate"] = round((time.perf_counter() - generate_started) * 1000, 3)
        answer, visualizations, visualization_warnings = extract_visualization(answer, selected) if answer_policy != "general" else (answer, [], [])
        warnings.extend(visualization_warnings)
        timings["total"] = round((time.perf_counter() - query_started) * 1000, 3)
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
        response_evidence = [] if answer_policy == "general" else selected
        result = QueryResult(
            request_id=request_id, answer=answer, answer_mode=provider.name,
            evidence=response_evidence, degraded=degraded,
            insufficient_evidence=not bool(selected),
            degradation_reason=",".join(degraded_reasons) or reason,
            warnings=warnings, corpus_incomplete=corpus_incomplete,
            normalized_query=normalized_query,
            structured_extraction=structured,
            generation=generation_meta,
            timings_ms=timings,
            visualizations=visualizations, retrieval_depth=retrieval_depth,
            answer_policy=answer_policy,
            retrieval_metrics={"bm25_candidates": len(lexical), "dense_candidates": len(dense),
                               "fused_candidates": len(fused), "final_evidence": len(selected),
                               "rerank": self._rerank_enabled(depth),
                               "max_context_evidence": depth["context"],
                               "source_participation": source_participation,
                               "embedding": embedding_meta},
        )
        self.store.finish_query(request_id, status="completed", answer_mode=provider.name,
                                degraded=degraded, insufficient_evidence=not bool(selected),
                                corpus_incomplete=corpus_incomplete, warnings=warnings, timings=timings,
                                generation_provider=generation_meta.get("provider"),
                                generation_model=generation_meta.get("model"),
                                prompt_version=generation_meta.get("prompt_version"),
                                answer_text=answer, retrieval_metrics=result.retrieval_metrics,
                                citation_validation=(citation_state if provider.name != "extractive_demo" else "degraded_extractive"),
                                generation_meta=result.generation)
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

    def delete_scoped_document(self, document_id: str, tenant_id: str, subject: str) -> dict | None:
        item = self.store.document_for_user(document_id, tenant_id, subject)
        if not item or item["scope_type"] not in {"private", "temporary"}:
            return None
        deleted_path = False
        if item.get("stored_path"):
            path = Path(item["stored_path"]).resolve()
            allowed = [(self.settings.var_dir / "private").resolve(),
                       (self.settings.var_dir / "temporary").resolve()]
            if not any(path.is_relative_to(root) for root in allowed):
                raise RagError("DOCUMENT_PATH_UNSAFE", "cleanup", "document path is outside managed roots")
            if path.is_file():
                path.unlink()
                deleted_path = True
        removed = self.store.unlink_document(document_id, tenant_id, subject)
        if removed:
            self.store.record_cleanup(document_id, "deleted", deleted_path=deleted_path,
                                      detail={"scope_type": item["scope_type"],
                                              "physical_deleted": removed["physical_deleted"]})
        return removed

    def delete_private_collection(self, collection_id: str, tenant_id: str, subject: str) -> bool:
        items = self.store.collection_documents(collection_id, tenant_id, subject)
        if items is None:
            return False
        for item in items:
            self.delete_scoped_document(item["document_id"], tenant_id, subject)
        return self.store.delete_private_collection(collection_id, tenant_id, subject)

    def delete_conversation(self, conversation_id: str, tenant_id: str, subject: str) -> bool:
        if not self.store.conversation(conversation_id, tenant_id, subject):
            return False
        temporary = next((item for item in self.store.accessible_collections(tenant_id, subject)
                          if item["scope_type"] == "temporary" and
                          item["conversation_id"] == conversation_id), None)
        if temporary:
            for item in self.store.collection_documents(
                    temporary["collection_id"], tenant_id, subject) or []:
                self.delete_scoped_document(item["document_id"], tenant_id, subject)
        return self.store.delete_conversation(conversation_id, tenant_id, subject)

    def cleanup_expired_documents(self) -> dict:
        items = self.store.expired_temporary_documents()
        deleted, failed = 0, []
        for item in items:
            try:
                if self.delete_scoped_document(item["document_id"], item["tenant_id"], item["owner_id"]):
                    deleted += 1
            except Exception as exc:
                failed.append({"document_id": item["document_id"],
                               "error_code": error_info(exc, "cleanup", self.settings.debug).error_code})
        return {"status": "completed" if not failed else "partial_failed",
                "expired": len(items), "deleted": deleted, "failed": failed}

    def promote_temporary_document(self, document_id: str, target_collection_id: str,
                                   tenant_id: str, subject: str) -> dict | None:
        source = self.store.document_for_user(document_id, tenant_id, subject)
        target = self.store.collection_for_owner(target_collection_id, tenant_id, subject)
        if not source or source["scope_type"] != "temporary" or not target or target["scope_type"] != "private":
            return None
        source_path = Path(source["stored_path"]).resolve()
        temp_root = (self.settings.var_dir / "temporary").resolve()
        if not source_path.is_relative_to(temp_root) or not source_path.is_file():
            raise RagError("DOCUMENT_SOURCE_UNAVAILABLE", "promote", "temporary PDF is unavailable")
        promoted_id = f"doc_{uuid.uuid4().hex}"
        destination = (self.settings.var_dir / "private" / tenant_id / subject /
                       promoted_id / source["source_name"]).resolve()
        private_root = (self.settings.var_dir / "private").resolve()
        if not destination.is_relative_to(private_root):
            raise RagError("DOCUMENT_PATH_UNSAFE", "promote", "promotion path is outside managed root")
        destination.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source_path, destination)
        return self.store.link_document(
            document_id=promoted_id, collection_id=target_collection_id,
            paper_id=source["paper_id"], tenant_id=tenant_id, owner_id=subject,
            scope_type="private", source_name=source["source_name"],
            stored_path=str(destination), status="ready",
        )

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
