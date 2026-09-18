from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from dataclasses import replace

import fitz

from fastapi.testclient import TestClient

from rag.models import Evidence
from rag.config import Settings
from rag.pipeline import Pipeline, validate_citation_boundary
from rag.parser import PdfParser
from rag.providers import OpenAICompatibleProvider
from rag.retriever import BM25Retriever, reciprocal_rank_fusion
from rag.security import SessionSecurity, hash_password
from rag.text import evidence_highlights, text_quality_issues, tokenize
from rag.visualizations import extract_visualization


def evidence(eid="ev_one", text="Cases a, b, and c had 10%, 20%, and 30% yield, respectively."):
    return Evidence(eid, "chk_1", "paper_1", "paper.pdf", 1, 1, "Results", text, 1.0)


def test_control_character_detection_preserves_signal():
    polluted = "yield 7\ue104uences 30%\ufffd"
    assert text_quality_issues(polluted) == ["CONTROL_OR_ENCODING_POLLUTION"]
    assert "30%" in polluted


def test_polluted_text_routes_to_validated_ocr_without_lossy_deletion(monkeypatch, tmp_path):
    pdf = tmp_path / "polluted.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "bad " + "original words " * 30)
    doc.save(pdf)
    doc.close()
    parser = PdfParser(ocr_enabled=True, min_page_chars=10)
    recovered = "recovered scientific text " * 25
    monkeypatch.setattr("rag.parser.text_quality_issues",
                        lambda text: ["CONTROL_OR_ENCODING_POLLUTION"] if "bad" in text else [])
    monkeypatch.setattr(parser, "_ocr_page", lambda page: (recovered, 0.97))
    result = parser.parse(pdf)
    assert result.pages[0].text == recovered
    assert result.pages[0].extraction_method == "ocr_quality_recovery"
    assert result.pages[0].status == "completed" and result.ocr_pages == 1
    assert not result.failed_pages


def test_quality_recovery_rejects_short_or_low_confidence_ocr():
    original = "word " * 100
    assert not PdfParser._acceptable_quality_recovery(original, "word " * 10, 0.99)
    assert not PdfParser._acceptable_quality_recovery(original, "word " * 90, 0.5)
    assert PdfParser._acceptable_quality_recovery(original, "word " * 70, 0.95)


def test_polluted_block_uses_local_ocr_without_replacing_the_page(monkeypatch):
    class Page:
        rect = fitz.Rect(0, 0, 100, 100)

        def get_text(self, kind):
            assert kind == "blocks"
            return [(10, 10, 90, 30, "Open circuit poten\x02al", 0, 0)]

    parser = PdfParser()
    monkeypatch.setattr(parser, "_ocr_clip", lambda page, rect: ("Open circuit potential", 0.99))
    text, confidence, count, attempted = parser._ocr_polluted_blocks(
        Page(), "Header\nOpen circuit poten\x02al\nUnchanged conclusion"
    )
    assert text == "Header\nOpen circuit potential\nUnchanged conclusion"
    assert confidence == 0.99 and count == 1 and attempted


def test_formula_control_character_is_not_guessed_by_ocr(monkeypatch):
    class Page:
        rect = fitz.Rect(0, 0, 100, 100)

        def get_text(self, kind):
            assert kind == "blocks"
            return [(10, 10, 90, 30, "G = H \x01 TS", 0, 0)]

    parser = PdfParser()

    def fail_if_called(page, rect):
        raise AssertionError("formula OCR must not run")

    monkeypatch.setattr(parser, "_ocr_clip", fail_if_called)
    text, confidence, count, attempted = parser._ocr_polluted_blocks(Page(), "G = H \x01 TS")
    assert text == "G = H \x01 TS"
    assert confidence is None and count == 0 and attempted


def test_bilingual_retrieval_terms_produce_safe_sentence_offsets():
    text = "Background sentence. Furfural yield reached 30% at 180 °C. Final note."
    result = evidence_highlights(text, ["糠醛", "产率"], ["furfural", "yield"])
    assert result["highlight_kind"] == "retrieval_sentence"
    assert result["highlight_terms"] == ["furfural", "yield"]
    marked = [text[x["start"]:x["end"]] for x in result["highlight_spans"]]
    assert marked == [" Furfural yield reached 30% at 180 °C."]
    assert all(0 <= x["start"] < x["end"] <= len(text) for x in result["highlight_spans"])


def test_citation_policy_boundaries_reject_leaks_and_unknown_ids():
    assert validate_citation_boundary("结论 [ev_one]", {"ev_one"}, "strict")[0]
    assert not validate_citation_boundary("结论 [ev_other]", {"ev_one"}, "strict")[0]
    assert validate_citation_boundary("模型通用知识（未经当前知识库验证）：说明", set(), "general")[0]
    assert not validate_citation_boundary("模型通用知识（未经当前知识库验证）：说明 [ev_one]", {"ev_one"}, "general")[0]
    assert not validate_citation_boundary("证据 [ev_one]\n未经当前知识库验证：[ev_one]", {"ev_one"}, "evidence_first")[0]


def test_chat_retries_one_transient_response_and_records_metadata(monkeypatch):
    statuses = iter([503, 200])

    class Response:
        def __init__(self, status): self.status = status
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    class Socket:
        def settimeout(self, value): self.timeout = value

    class Connection:
        def __init__(self, *args, **kwargs): self.sock = Socket()
        def connect(self): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response(next(statuses))
        def close(self): pass

    monkeypatch.setattr("rag.providers.http.client.HTTPConnection", Connection)
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.url, provider.key, provider.model = "http://chat.test/v1/chat/completions", "secret", "model"
    provider.connect_timeout, provider.read_timeout, provider.total_timeout = 1, 2, 3
    provider.last_call = {}
    assert provider._request({"model": "model"}) == "ok"
    assert provider.last_call["attempts"] == 2 and provider.last_call["retries"] == 1
    assert {"connect_ms", "headers_ms", "body_ms", "duration_ms"} <= provider.last_call.keys()


def test_quantitative_comparison_prompt_requires_one_validated_chart():
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.model = "model"
    captured = {}
    provider._request = lambda payload: captured.setdefault("payload", payload) or "ok"
    provider.answer("compare three yields", [evidence()])
    system = captured["payload"]["messages"][0]["content"]
    assert "when, and only when" in system
    assert "at least three quantitative values" in system


def test_failed_citation_repair_never_exposes_unvalidated_draft(monkeypatch, tmp_path):
    pdf = tmp_path / "data" / "paper.pdf"
    pdf.parent.mkdir()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Furfural yield was 30 percent in the reported experiment.")
    doc.save(pdf)
    doc.close()
    base = Settings.load()
    cfg = replace(base, data_dir=pdf.parent, var_dir=tmp_path / "var",
                  db_path=tmp_path / "var/rag.sqlite3", log_path=tmp_path / "var/rag.jsonl",
                  min_page_chars=10, chat_provider="openai_compatible",
                  embedding_provider="disabled", structured_extraction_enabled=False)

    class InvalidProvider:
        name, model, prompt_version, last_call = "fake_chat", "fake", "test-v1", {"attempts": 2, "retries": 1}
        def answer(self, question, evidence, *, answer_policy="strict"):
            return "UNSAFE MODEL DRAFT [ev_not_allowed]"
        def repair_citations(self, question, evidence, draft):
            return "STILL UNSAFE [ev_not_allowed]"

    monkeypatch.setattr("rag.pipeline.configured_provider", lambda settings: InvalidProvider())
    pipeline = Pipeline(cfg)
    pipeline.ingest()
    result = pipeline.query("furfural yield", answer_policy="strict")
    assert result.answer_mode == "extractive_demo"
    assert "UNSAFE" not in result.answer
    assert "CITATION_BINDING_INVALID" in result.degradation_reason
    diagnostic = pipeline.store.query_diagnostic(result.request_id)
    assert diagnostic["citation_validation"] == "degraded_extractive"
    assert diagnostic["generation_provider"] == "fake_chat"
    assert diagnostic["generation_meta"]["answer_provider"] == "extractive_demo"
    assert diagnostic["generation_meta"]["retries"] == 1


def test_three_answer_policies_keep_general_knowledge_uncited(monkeypatch, tmp_path):
    pdf = tmp_path / "data" / "paper.pdf"
    pdf.parent.mkdir()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Furfural yield was 30 percent in the reported experiment.")
    doc.save(pdf)
    doc.close()
    base = Settings.load()
    cfg = replace(base, data_dir=pdf.parent, var_dir=tmp_path / "var",
                  db_path=tmp_path / "var/rag.sqlite3", log_path=tmp_path / "var/rag.jsonl",
                  min_page_chars=10, chat_provider="openai_compatible",
                  embedding_provider="disabled", structured_extraction_enabled=False)
    calls = []

    class PolicyProvider:
        name, model, prompt_version, last_call = "fake_chat", "fake", "test-v1", {"attempts": 1, "retries": 0}
        def answer(self, question, evidence, *, answer_policy="strict"):
            calls.append((answer_policy, len(evidence)))
            if answer_policy == "general":
                return "模型通用知识（未经当前知识库验证）：一般说明。"
            eid = evidence[0].evidence_id
            if answer_policy == "evidence_first":
                return f"证据结论 [{eid}]\n\n未经当前知识库验证\n一般说明。"
            return f"证据结论 [{eid}]"

    provider = PolicyProvider()
    monkeypatch.setattr("rag.pipeline.configured_provider", lambda settings: provider)
    pipeline = Pipeline(cfg)
    pipeline.ingest()
    strict = pipeline.query("furfural yield", answer_policy="strict")
    evidence_first = pipeline.query("furfural yield", answer_policy="evidence_first")
    general = pipeline.query("furfural yield", answer_policy="general")
    assert strict.evidence and "[ev_" in strict.answer
    assert evidence_first.evidence and "未经当前知识库验证" in evidence_first.answer
    assert not general.evidence and "[ev_" not in general.answer
    assert calls == [("strict", 1), ("evidence_first", 1), ("general", 0)]
    assert pipeline.store.query_diagnostic(general.request_id)["retrieval_metrics"]["final_evidence"] == 1


def test_visualization_requires_three_evidence_verified_rows():
    ev = evidence()
    answer = 'Compared [ev_one]\n```json\n{"type":"table","title":"Yields","columns":["case","yield","evidence"],"rows":[["a",10,"ev_one"],["b",20,"ev_one"],["c",30,"ev_one"]]}\n```'
    clean, visuals, warnings = extract_visualization(answer, [ev])
    assert clean == "Compared [ev_one]" and len(visuals) == 1 and not warnings


def test_visualization_rejects_unverified_value_and_malicious_text():
    answer = 'Safe [ev_one]\n```json\n{"type":"bar","title":"<script>x</script>","columns":["case","yield","evidence"],"rows":[["a",999,"ev_one"],["b",20,"ev_one"],["c",30,"ev_one"]]}\n```'
    clean, visuals, warnings = extract_visualization(answer, [evidence()])
    assert clean == "Safe [ev_one]" and not visuals and warnings[0]["code"] == "VISUALIZATION_INVALID"


def test_visualization_rejects_wrong_unit_semantics_and_extreme_values():
    ev = evidence(text="Cases a, b, and c were tested at 10, 20, and 30 min; yield was not reported.")
    wrong_unit = 'Safe [ev_one]\n```json\n{"type":"bar","title":"Yield","columns":["case","yield","evidence"],"rows":[["a",10,"ev_one"],["b",20,"ev_one"],["c",30,"ev_one"]]}\n```'
    assert not extract_visualization(wrong_unit, [ev])[1]
    huge = 'Safe [ev_one]\n```json\n{"type":"bar","title":"Yield","columns":["case","yield","evidence"],"rows":[["a",10000000000000,"ev_one"],["b",20,"ev_one"],["c",30,"ev_one"]]}\n```'
    assert not extract_visualization(huge, [evidence()])[1]


def test_visualization_accepts_shared_unit_for_values_in_one_evidence_sentence():
    ev = evidence(text="Maximum furfural yields at 120, 130, and 140 °C were 66.5, 71.1, and 70.2%, respectively.")
    answer = 'Compared [ev_one]\n```json\n{"type":"bar","title":"Furfural yield","columns":["temperature","yield","evidence"],"rows":[["120 °C",66.5,"ev_one"],["130 °C",71.1,"ev_one"],["140 °C",70.2,"ev_one"]]}\n```'
    clean, visuals, warnings = extract_visualization(answer, [ev])
    assert clean == "Compared [ev_one]" and len(visuals) == 1 and not warnings


def test_depth_and_policy_contracts_are_centralized():
    assert set(Pipeline.DEPTHS) == {"fast", "standard", "deep"}
    assert Pipeline.DEPTHS["fast"]["bm25"] < Pipeline.DEPTHS["standard"]["bm25"] < Pipeline.DEPTHS["deep"]["bm25"]
    assert Pipeline.POLICIES == {"strict", "evidence_first", "general"}


def test_frontend_has_immediate_state_retry_and_safe_events():
    js = (Path(__file__).parents[1] / "src/rag/web/app.js").read_text(encoding="utf-8")
    assert "pending:true" in js and "问题已保留，可重试" in js
    assert "AbortController" in js and "!e.shiftKey" in js
    assert "onclick=\"showEvidence" not in js and "data-evidence" in js
    assert "numberedAnswer" in js and "citation-unknown" in js
    assert "chartCitation" in js and "data-evidence" in js
    assert "pendingLatencyMs" in js


def test_openapi_exposes_query_modes(monkeypatch, tmp_path):
    import rag.api as api
    from rag.config import Settings
    from dataclasses import replace

    base = Settings.load()
    local = replace(base, data_dir=tmp_path / "data", var_dir=tmp_path / "var",
                    db_path=tmp_path / "var/rag.sqlite3", log_path=tmp_path / "var/logs/rag.jsonl")
    monkeypatch.setattr(api, "pipeline", Pipeline(local))
    with TestClient(api.app) as client:
        schema = client.get("/openapi.json").json()
    props = schema["components"]["schemas"]["QueryRequest"]["properties"]
    assert {"retrieval_depth", "answer_policy"} <= set(props)


def test_generation_capacity_exhaustion_returns_retryable_503(monkeypatch, tmp_path):
    import rag.api as api

    base = Settings.load()
    local = replace(base, data_dir=tmp_path / "data", var_dir=tmp_path / "var",
                    db_path=tmp_path / "var/rag.sqlite3", log_path=tmp_path / "var/rag.jsonl")

    class Full:
        def acquire(self, blocking=False): return False
        def release(self): raise AssertionError("unacquired slot must not be released")

    monkeypatch.setattr(api, "pipeline", Pipeline(local))
    monkeypatch.setattr(api, "generation_slots", Full())
    with TestClient(api.app) as client:
        response = client.post("/v1/query", json={"question": "furfural"})
    assert response.status_code == 503
    assert response.json()["error"]["error_code"] == "GENERATION_BUSY"
    assert response.json()["error"]["retryable"] is True


def test_admin_quality_repair_backs_up_before_queueing(monkeypatch, tmp_path):
    import rag.api as api

    data = tmp_path / "data"
    data.mkdir()
    pdf = data / "paper.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Furfural yield was 30 percent in a controlled experiment.")
    doc.save(pdf)
    doc.close()
    base = Settings.load()
    cfg = replace(base, data_dir=data, var_dir=tmp_path / "var",
                  db_path=tmp_path / "var/rag.sqlite3", log_path=tmp_path / "var/rag.jsonl",
                  min_page_chars=10, chat_provider="extractive", embedding_provider="disabled")
    pipeline = Pipeline(cfg)
    pipeline.ingest()
    paper_id = pipeline.store.papers()[0]["paper_id"]
    with pipeline.store.connect() as conn:
        conn.execute("UPDATE pages SET text=text || ? WHERE paper_id=?", ("\ue103", paper_id))
        conn.execute("UPDATE chunks SET text=text || ? WHERE paper_id=?", ("\ue103", paper_id))
    monkeypatch.setenv("RAG_ADMIN_PASSWORD_HASH", hash_password("test-password", iterations=1000))
    monkeypatch.setenv("RAG_SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(api, "pipeline", pipeline)
    monkeypatch.setattr(api, "security", SessionSecurity())
    with TestClient(api.app) as client:
        assert client.post("/v1/auth/login", json={"password": "test-password"}).status_code == 200
        quality = client.get("/v1/admin/pdf-quality").json()
        assert quality["affected_papers"] == quality["affected_pages"] == quality["affected_chunks"] == 1
        queued = client.post("/v1/admin/pdf-quality/repair")
        assert queued.status_code == 202 and len(queued.json()["jobs"]) == 1
        assert queued.json()["jobs"][0]["paper_id"] == paper_id
        backup = Path(queued.json()["backup"]["path"])
        assert backup.exists() and backup != cfg.db_path
        with sqlite3.connect(backup) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT count(*) FROM papers WHERE paper_id=?", (paper_id,)).fetchone()[0] == 1


def test_forced_rebuild_preserves_official_private_and_temporary_links(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    pdf = data / "paper.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Furfural yield from xylose was 30 percent.")
    doc.save(pdf)
    doc.close()
    base = Settings.load()
    cfg = replace(base, data_dir=data, var_dir=tmp_path / "var",
                  db_path=tmp_path / "var/rag.sqlite3", log_path=tmp_path / "var/rag.jsonl",
                  min_page_chars=10, chat_provider="extractive", embedding_provider="disabled")
    pipeline = Pipeline(cfg)
    pipeline.ingest()
    paper_id = pipeline.store.papers()[0]["paper_id"]
    private = pipeline.store.create_private_collection("default", "alice", "private")
    conversation = pipeline.store.create_conversation("default", "alice", "temporary")
    temp_collection = f"col_temp_{conversation['conversation_id'][5:]}"
    pipeline.store.link_document(document_id="doc_private", collection_id=private["collection_id"],
                                 paper_id=paper_id, tenant_id="default", owner_id="alice",
                                 scope_type="private", source_name=pdf.name, stored_path=str(pdf))
    pipeline.store.link_document(document_id="doc_temporary", collection_id=temp_collection,
                                 paper_id=paper_id, tenant_id="default", owner_id="alice",
                                 scope_type="temporary", source_name=pdf.name, stored_path=str(pdf),
                                 conversation_id=conversation["conversation_id"])
    pipeline.ingest(file=pdf, force=True)
    with pipeline.store.connect() as conn:
        links = {(row["document_id"], row["scope_type"]) for row in conn.execute(
            "SELECT document_id,scope_type FROM collection_documents WHERE paper_id=?", (paper_id,)
        )}
    assert links == {(f"doc_{paper_id}", "official"), ("doc_private", "private"),
                     ("doc_temporary", "temporary")}


def test_numeric_literals_tokenize_identically_with_and_without_a_unit_suffix():
    # A user types "71.1%", a PDF writes "71.1 %".  Both must produce the same token.
    assert tokenize("71.1%") == tokenize("71.1 %") == ["71.1"]
    assert tokenize("130°C") == tokenize("130 °C") == ["130", "c"]
    assert tokenize("8.19 wt%") == ["8.19", "wt"]
    assert "71.1%" not in tokenize("the yield was 71.1 % at 130 °C")


def test_question_numeric_literal_decides_which_topical_document_ranks_first():
    def chunk(cid, text):
        return {"chunk_id": cid, "paper_id": "p", "paper_name": "p.pdf", "page_start": 1,
                "page_end": 1, "section_path": "", "text": text}

    topical = ("Furfural yield from xylose increased with reaction temperature and residence time; "
               "the catalyst loading was varied at 130 C. ")
    chunks = [chunk("answer", "The maximum furfural yields at 120, 130, and 140 C were 66.5, 71.1, "
                              "and 70.2%, obtained at 1.5, 1.0, and 0.5 h respectively.")]
    chunks += [chunk(f"decoy_{i}", topical * 8) for i in range(6)]
    hits = BM25Retriever(chunks).search("Which experiment reported 71.1% furfural yield at 130 C?", 8)
    assert hits[0].chunk_id == "answer", "the page holding 71.1 must outrank same-topic pages"


def test_fusion_reports_rrf_scores_without_overwriting_source_retriever_scores():
    lexical = [Evidence("ev_bm25", "chk_a", "p", "p.pdf", 1, 1, "", "text", 13.3337),
               Evidence("ev_bm25_b", "chk_b", "p", "p.pdf", 1, 1, "", "text", 9.5)]
    dense = [Evidence("ev_dense", "chk_a", "p", "p.pdf", 1, 1, "", "text", 0.87),
             Evidence("ev_dense_c", "chk_c", "p", "p.pdf", 1, 1, "", "text", 0.81)]
    fused = reciprocal_rank_fusion(lexical, dense, top_k=5)
    # query_candidates is written from these lists after fusion, so they must stay untouched
    assert [item.score for item in lexical] == [13.3337, 9.5]
    assert [item.evidence_id for item in lexical] == ["ev_bm25", "ev_bm25_b"]
    assert [item.score for item in dense] == [0.87, 0.81]
    assert fused[0].chunk_id == "chk_a" and abs(fused[0].score - 2 / 61) < 1e-8
    assert fused[0].evidence_id not in {"ev_bm25", "ev_dense"}


def test_rerank_metric_agrees_between_refusal_and_normal_paths(tmp_path):
    cfg = replace(Settings.load(), var_dir=tmp_path, db_path=tmp_path / "rag.sqlite3",
                  log_path=tmp_path / "rag.jsonl", reranker_provider="disabled")
    pipeline = Pipeline(cfg)
    standard = Pipeline.DEPTHS["standard"]
    assert standard["rerank"] is True                       # the depth asks for a reranker ...
    assert pipeline._rerank_enabled(standard) is False      # ... but none is configured
    assert pipeline._rerank_enabled(Pipeline.DEPTHS["fast"]) is False
