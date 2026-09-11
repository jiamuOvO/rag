from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import fitz
from fastapi.testclient import TestClient

from rag.config import Settings
from rag.pipeline import Pipeline
from rag.security import SessionSecurity, hash_password
from rag.errors import RagError


def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    var = tmp_path / "var"
    return Settings(
        project_dir=tmp_path, data_dir=data, var_dir=var, db_path=var / "rag.sqlite3",
        log_path=var / "logs" / "rag.jsonl", debug=False, ocr_enabled=False,
        min_page_chars=20, chunk_tokens=80, chunk_overlap=10, chat_provider="extractive",
        chat_base_url=None, chat_api_key=None, chat_model=None, chat_timeout_seconds=5,
    )


def make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "RESULTS\nXylose conversion produced furfural with a yield of 78 percent at 180 C.")
    doc.save(path)
    doc.close()


def test_end_to_end_and_idempotency(tmp_path: Path):
    cfg = settings(tmp_path)
    pdf = cfg.data_dir / "paper.pdf"
    make_pdf(pdf)
    pipeline = Pipeline(cfg)
    first = pipeline.ingest()
    assert first["status"] == "completed"
    assert first["counts"]["succeeded"] == 1
    result = pipeline.query("furfural yield from xylose")
    assert not result.insufficient_evidence
    assert result.degraded and "CHAT_NOT_CONFIGURED" in result.degradation_reason
    assert "EMBEDDING_DISABLED" in result.degradation_reason
    assert result.evidence[0].paper_name == "paper.pdf"
    historical_candidates = pipeline.store.query_diagnostic(result.request_id)["candidates"]
    second = pipeline.ingest()
    assert second["counts"]["skipped"] == 1
    assert len(pipeline.store.papers()) == 1
    rebuilt = pipeline.ingest(force=True)
    assert rebuilt["status"] == "completed"
    assert rebuilt["counts"]["succeeded"] == 1
    assert len(pipeline.store.chunks()) > 0
    assert pipeline.store.query_diagnostic(result.request_id)["candidates"] == historical_candidates
    pages = pipeline.store.pages(pipeline.store.papers()[0]["paper_id"])
    assert len(pages) == 1
    assert pages[0]["extraction_method"] == "text"
    assert pages[0]["chunk_ids"]
    assert pipeline.store.paper_chunks(pipeline.store.papers()[0]["paper_id"])[0]["ordinal"] == 0
    assert pipeline.store.run(rebuilt["run_id"])["stages"]
    parse_stage = next(x for x in pipeline.store.run(rebuilt["run_id"])["stages"] if x["stage"] == "parse")
    assert parse_stage["paper_id"] and parse_stage["component_version"] == "pymupdf-rapidocr-v1"


def test_paper_scope_does_not_expand(tmp_path: Path):
    cfg = settings(tmp_path)
    make_pdf(cfg.data_dir / "paper.pdf")
    pipeline = Pipeline(cfg)
    pipeline.ingest()
    result = pipeline.query("furfural", paper_ids=["paper_not_present"])
    assert result.insufficient_evidence
    unrelated = pipeline.query("quantum entanglement lifetime for zebra qubits")
    assert unrelated.insufficient_evidence and unrelated.answer_mode == "refusal"


def test_api_health_and_validation(tmp_path: Path, monkeypatch):
    cfg = settings(tmp_path)
    make_pdf(cfg.data_dir / "paper.pdf")
    local = Pipeline(cfg)
    import rag.api as api
    monkeypatch.setattr(api, "pipeline", local)
    client = TestClient(api.app)
    assert client.get("/health").status_code == 200
    ready = client.get("/ready").json()
    assert "data_dir" not in ready and "database" not in ready
    invalid = client.post("/v1/query", json={"question": ""})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"
    assert invalid.headers["x-request-id"].startswith("req_")
    response = client.get("/v1/papers")
    assert response.status_code == 200 and response.json()["count"] == 0
    assert client.get("/v1/papers?limit=0").status_code == 400
    assert client.get("/").status_code == 200
    assert client.post("/v1/ingestions", json={}).status_code == 503
    assert client.get("/v1/runs/run_missing").status_code == 503


def test_persistent_job_marks_interrupted_on_restart(tmp_path: Path):
    pipeline = Pipeline(settings(tmp_path))
    pipeline.store.create_ingestion_job("run_test", "req_test", file_path=None, force=False)
    claimed = pipeline.store.claim_ingestion_job()
    assert claimed and pipeline.store.run("run_test")["status"] == "running"
    pipeline.store.start_stage("run_test", "parse", paper_id="paper_test")
    assert pipeline.store.recover_interrupted_runs() == 1
    run = pipeline.store.run("run_test")
    assert run["status"] == "interrupted"
    assert run["error"]["error_code"] == "TASK_INTERRUPTED"
    assert run["stages"][0]["status"] == "interrupted"


def test_duplicate_active_ingestion_is_detectable(tmp_path: Path):
    pipeline = Pipeline(settings(tmp_path))
    pipeline.store.create_ingestion_job("run_hash", "req_hash", file_path="queued.pdf",
                                        force=False, source_hash="abc")
    assert pipeline.store.active_ingestion_for_hash("abc")["run_id"] == "run_hash"
    pipeline.store.create_ingestion_job("run_all", "req_all", file_path=None, force=False)
    assert pipeline.store.active_full_ingestion()["run_id"] == "run_all"


def test_failed_run_retry_requires_admin_and_preserves_lineage(tmp_path: Path, monkeypatch):
    import rag.api as api

    local = Pipeline(settings(tmp_path))
    local.store.create_ingestion_job("run_failed", "req_failed", file_path=None, force=True)
    local.store.update_run("run_failed", status="failed", finished=True)
    monkeypatch.setenv("RAG_ADMIN_PASSWORD_HASH", hash_password("test-password", iterations=1000))
    monkeypatch.setenv("RAG_SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(api, "pipeline", local)
    monkeypatch.setattr(api, "security", SessionSecurity())
    api.app.state.worker = SimpleNamespace(notify=lambda: None)
    client = TestClient(api.app)

    assert client.post("/v1/runs/run_failed/retry").status_code == 401
    assert client.post("/v1/auth/login", json={"password": "test-password"}).status_code == 200
    retried = client.post("/v1/runs/run_failed/retry")
    assert retried.status_code == 202
    payload = retried.json()
    assert payload["parent_run_id"] == "run_failed" and payload["status"] == "queued"
    assert local.store.job_for_run(payload["run_id"])["parent_run_id"] == "run_failed"


def test_failed_new_paper_is_visible_without_fake_success(tmp_path: Path):
    cfg = settings(tmp_path)
    broken = cfg.data_dir / "broken.pdf"
    broken.write_bytes(b"%PDF-broken")
    pipeline = Pipeline(cfg)
    run = pipeline.ingest(file=broken)
    assert run["status"] == "failed"
    paper = pipeline.store.papers()[0]
    assert paper["status"] == "failed" and paper["page_count"] == 0
    assert paper["last_error"]["error_code"] == "PDF_OPEN_FAILED"
    result = pipeline.query("xylose furfural yield")
    assert result.insufficient_evidence and result.corpus_incomplete


def test_query_diagnostic_rerank_and_structured_extraction(tmp_path: Path):
    cfg = replace(settings(tmp_path), reranker_provider="lexical_coverage",
                  structured_extraction_enabled=True)
    make_pdf(cfg.data_dir / "paper.pdf")
    pipeline = Pipeline(cfg)
    pipeline.ingest()
    result = pipeline.query("xylose furfural yield")
    assert result.structured_extraction
    run_id = result.structured_extraction["extraction_run_id"]
    stored = pipeline.store.extraction(run_id)
    assert stored and stored["schema_version"] == "reaction-evidence-v1"
    diagnostic = pipeline.store.query_diagnostic(result.request_id)
    assert diagnostic and any(x["retriever"] == "rerank" for x in diagnostic["candidates"])
    assert diagnostic["answer_text"] == result.answer


def test_authenticated_browser_upload_and_review_flow(tmp_path: Path, monkeypatch):
    import time
    import rag.api as api

    cfg = settings(tmp_path)
    local = Pipeline(cfg)
    monkeypatch.setenv("RAG_ADMIN_PASSWORD_HASH", hash_password("test-password", iterations=1000))
    monkeypatch.setenv("RAG_SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(api, "pipeline", local)
    monkeypatch.setattr(api, "security", SessionSecurity())
    upload = tmp_path / "browser.pdf"
    make_pdf(upload)
    with TestClient(api.app) as client:
        assert client.get("/").status_code == 200
        assert client.post("/v1/uploads", content=upload.read_bytes(),
                           headers={"X-Filename": "browser.pdf", "Content-Type": "application/pdf"}).status_code == 401
        assert client.post("/v1/auth/login", json={"password": "test-password"}).status_code == 200
        assert client.post("/v1/uploads", content=upload.read_bytes(),
                           headers={"X-Filename": "browser.pdf", "Content-Type": "text/plain"}).status_code == 415
        assert client.post("/v1/uploads", content=b"%PDF-broken",
                           headers={"X-Filename": "broken.pdf", "Content-Type": "application/pdf"}).status_code == 400
        created = client.post("/v1/uploads", content=upload.read_bytes(),
                              headers={"X-Filename": "browser.pdf", "Content-Type": "application/pdf"})
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        for _ in range(50):
            run = client.get(f"/v1/runs/{run_id}").json()
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert run["status"] == "completed"
        paper_id = local.store.papers()[0]["paper_id"]
        listed = client.get("/v1/papers").json()["items"][0]
        assert "file_path" not in listed and "source_hash" not in listed
        assert client.get("/v1/papers?q=browser&status=ready").json()["count"] == 1
        assert client.get("/v1/runs").status_code == 200
        assert client.get(f"/v1/papers/{paper_id}/pages").json()["count"] == 1
        assert client.get(f"/v1/papers/{paper_id}/pdf").status_code == 200
        answer = client.post("/v1/query", json={"question": "xylose furfural yield"}).json()
        assert answer["request_id"]
        frontend = client.get("/assets/app.js").text
        assert "回查原文" in frontend and "reviewEvidence" in frontend
        homepage = client.get("/").text
        assert all(label in homepage for label in (
            "历史会话", "我的研究库", "临时 PDF", "文档处理", "检索测试", "运行日志"
        ))
        diagnostic = client.get(f"/v1/queries/{answer['request_id']}")
        assert diagnostic.status_code == 200 and diagnostic.json()["candidates"]
        recent_queries = client.get("/v1/queries")
        assert recent_queries.status_code == 200
        assert answer["request_id"] in {item["request_id"] for item in recent_queries.json()["items"]}


def test_model_embedding_and_reranker_failures_are_explicit(tmp_path: Path, monkeypatch):
    import rag.pipeline as pipeline_module

    cfg = replace(settings(tmp_path), reranker_provider="broken")
    make_pdf(cfg.data_dir / "paper.pdf")
    pipeline = Pipeline(cfg)
    pipeline.ingest()

    class BrokenEmbedding:
        name, model = "broken", "broken-v1"

        def embed(self, _):
            raise RagError("EMBEDDING_TEST_FAILURE", "embedding", "unavailable", retryable=True)

    class InvalidCitationChat:
        name = "fake_chat"

        def answer(self, question, evidence):
            return "Unsupported citation [ev_not_in_context]"

    monkeypatch.setattr(pipeline_module, "configured_embedding_provider", lambda _: BrokenEmbedding())
    monkeypatch.setattr(pipeline_module, "configured_provider", lambda _: InvalidCitationChat())
    result = pipeline.query("xylose furfural yield")
    assert result.degraded and result.answer_mode == "extractive_demo"
    assert "EMBEDDING_TEST_FAILURE" in result.degradation_reason
    assert "RERANK_FAILED" in result.degradation_reason
    assert "CITATION_BINDING_INVALID" in result.degradation_reason
    log_text = cfg.log_path.read_text(encoding="utf-8")
    assert "EMBEDDING_TEST_FAILURE" in log_text
    assert "RERANK_FAILED" in log_text
    assert "CITATION_BINDING_INVALID" in log_text


def test_partial_page_failure_is_visible_in_query(tmp_path: Path):
    cfg = replace(settings(tmp_path), min_page_chars=1000)
    make_pdf(cfg.data_dir / "paper.pdf")
    pipeline = Pipeline(cfg)
    run = pipeline.ingest()
    assert run["status"] == "partial_failed"
    assert pipeline.store.papers()[0]["status"] == "partial_failed"
    result = pipeline.query("xylose furfural yield")
    assert result.corpus_incomplete
    assert any(item["code"] == "CORPUS_PARTIAL" for item in result.warnings)
    assert "OCR_DISABLED" in cfg.log_path.read_text(encoding="utf-8")


def test_ocr_page_has_persistent_stage_timing(tmp_path: Path, monkeypatch):
    cfg = replace(settings(tmp_path), ocr_enabled=True, min_page_chars=1000)
    make_pdf(cfg.data_dir / "paper.pdf")
    pipeline = Pipeline(cfg)
    monkeypatch.setattr(pipeline.parser, "_ocr_page", lambda page: ("OCR recovered xylose furfural yield 70%", 0.91))
    run = pipeline.ingest()
    stage = next(item for item in run["stages"] if item["stage"] == "ocr")
    assert stage["status"] == "completed" and stage["paper_id"]
    assert stage["component_version"] == "rapidocr-onnxruntime-1.4.4"
    assert stage["duration_ms"] >= 0


def test_normal_generated_answer_uses_only_current_evidence_ids(tmp_path: Path, monkeypatch):
    import rag.pipeline as pipeline_module

    class FakeEmbedding:
        name, model = "fake", "fake-v1"

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    class CitingChat:
        name = "citing_chat"

        def answer(self, question, evidence):
            return f"The evidence reports a furfural result [{evidence[0].evidence_id}]."

    cfg = replace(settings(tmp_path), embedding_provider="fake", embedding_model="fake-v1")
    monkeypatch.setattr(pipeline_module, "configured_embedding_provider", lambda _: FakeEmbedding())
    monkeypatch.setattr(pipeline_module, "configured_provider", lambda _: CitingChat())
    make_pdf(cfg.data_dir / "paper.pdf")
    pipeline = Pipeline(cfg)
    pipeline.ingest()
    result = pipeline.query("xylose furfural yield")
    assert result.answer_mode == "citing_chat" and not result.degraded
    assert f"[{result.evidence[0].evidence_id}]" in result.answer
    rebuilt = pipeline.rebuild_embeddings()
    assert rebuilt["status"] == "completed"
    previous_count = pipeline.store.embedding_stats()["count"]

    class BrokenRebuild(FakeEmbedding):
        def embed(self, texts):
            raise RagError("EMBED_REBUILD_FAILED", "embedding", "failed")

    monkeypatch.setattr(pipeline_module, "configured_embedding_provider", lambda _: BrokenRebuild())
    failed = pipeline.rebuild_embeddings()
    assert failed["status"] == "failed"
    assert pipeline.store.embedding_stats()["count"] == previous_count
