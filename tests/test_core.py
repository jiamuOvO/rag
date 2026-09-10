from __future__ import annotations

from pathlib import Path

from rag.chunker import chunk_pages
from rag.config import Settings
from rag.errors import RagError, error_info, redact
from rag.models import Page
from rag.pipeline import file_hash, paper_id
from rag.retriever import BM25Retriever, DenseRetriever, analyze_query, query_tokens, reciprocal_rank_fusion
from rag.text import tokenize
from rag.security import hash_password, verify_password
from rag.extraction import EXTRACTION_JSON_SCHEMA, extract_reaction_evidence
from rag.models import Evidence
from rag.reranker import LexicalCoverageReranker
from rag.store import Store


def test_stable_content_id(tmp_path: Path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    assert paper_id(file_hash(first)) == paper_id(file_hash(second))


def test_tokenizer_preserves_chemical_values_and_chinese_bigrams():
    tokens = tokenize("HMF yield 82.5% at 180°C 木糖制糠醛")
    assert "hmf" in tokens
    assert "82.5%" in tokens
    assert "180°c" in tokens
    assert "木糖" in tokens
    assert "糠醛" in tokens
    assert "furfural" in query_tokens("糠醛收率")
    assert "yield" in query_tokens("糠醛收率")
    analysis = analyze_query("糠醛收率")
    assert "furfural" in analysis["expansions"] and "yield" in analysis["expansions"]


def test_chunk_links_and_overlap():
    pages = [Page(1, "INTRODUCTION\n\n" + "alpha beta gamma delta. " * 20, "text")]
    chunks = chunk_pages(pages, "paper_x", "x.pdf", target_tokens=20, overlap_tokens=5)
    assert len(chunks) > 1
    assert chunks[0].next_chunk_id == chunks[1].chunk_id
    assert chunks[1].previous_chunk_id == chunks[0].chunk_id
    assert set(tokenize(chunks[0].text)) & set(tokenize(chunks[1].text))
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.overlap_tokens == 5 for chunk in chunks)


def test_bm25_ranks_relevant_chunk_and_formats_evidence():
    chunks = [
        {"chunk_id": "c1", "paper_id": "p1", "paper_name": "a.pdf", "page_start": 1,
         "page_end": 1, "section_path": "Results", "text": "Furfural yield reached 80% from xylose."},
        {"chunk_id": "c2", "paper_id": "p2", "paper_name": "b.pdf", "page_start": 2,
         "page_end": 2, "section_path": "Methods", "text": "Microscopy images of cellulose."},
    ]
    found = BM25Retriever(chunks).search("xylose furfural yield", 2)
    assert found[0].chunk_id == "c1"
    assert found[0].evidence_id.startswith("ev_")
    assert found[0].page_start == 1
    assert BM25Retriever(chunks).search("what was reported for quantum zebras", 2) == []


def test_dense_search_and_rrf():
    import numpy as np
    chunks = [
        {"chunk_id": "c1", "paper_id": "p1", "paper_name": "a.pdf", "page_start": 1,
         "page_end": 1, "section_path": "", "text": "furfural", "dimensions": 2,
         "vector": np.asarray([1.0, 0.0], dtype=np.float32).tobytes()},
        {"chunk_id": "c2", "paper_id": "p2", "paper_name": "b.pdf", "page_start": 2,
         "page_end": 2, "section_path": "", "text": "cellulose", "dimensions": 2,
         "vector": np.asarray([0.0, 1.0], dtype=np.float32).tobytes()},
    ]
    dense = DenseRetriever(chunks).search([0.9, 0.1], 2)
    assert dense[0].chunk_id == "c1"
    lexical_chunks = [{k: v for k, v in chunk.items() if k not in {"dimensions", "vector"}}
                      for chunk in chunks]
    lexical = BM25Retriever(lexical_chunks).search("cellulose", 2)
    fused = reciprocal_rank_fusion(lexical, dense, top_k=2)
    assert {item.chunk_id for item in fused} == {"c1", "c2"}


def test_redaction_and_error_shape():
    safe = redact("Authorization: Bearer-secret api_key=abc123 password=hunter2 sk-123456789")
    assert "abc123" not in safe and "hunter2" not in safe and "sk-123456789" not in safe
    info = error_info(RagError("X", "parse", "bad", retryable=True), "parse")
    assert info.error_code == "X" and info.stage == "parse" and info.retryable


def test_yaml_reads_non_secret_config_and_secret_from_environment(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(
        "chat:\n  provider: openai_compatible\n  base_url: https://example.test/v1\n"
        "  model: demo-model\n  api_key_env: TEST_RAG_SECRET\n"
        "embedding:\n  provider: openai_compatible\n  base_url: https://embedding.test/v1\n"
        "  model: embedding-model\n  api_key_env: TEST_RAG_SECRET\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RAG_CONFIG_FILE", str(config))
    monkeypatch.setenv("TEST_RAG_SECRET", "secret-value")
    loaded = Settings.load()
    assert loaded.chat_model == "demo-model"
    assert loaded.chat_base_url == "https://example.test/v1"
    assert loaded.embedding_base_url == "https://embedding.test/v1"
    assert loaded.embedding_model == "embedding-model"
    assert loaded.chat_api_key == "secret-value"
    assert loaded.embedding_api_key == "secret-value"


def test_yaml_rejects_plaintext_api_key(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("chat:\n  api_key: do-not-store-this\n", encoding="utf-8")
    monkeypatch.setenv("RAG_CONFIG_FILE", str(config))
    try:
        Settings.load()
    except ValueError as exc:
        assert "Do not store api_key" in str(exc)
    else:
        raise AssertionError("plaintext YAML secret should be rejected")


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple", iterations=1000)
    second = hash_password("correct horse battery staple", iterations=1000)
    assert first != second
    assert "correct horse" not in first
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong", first)


def test_structured_extraction_keeps_raw_value_unit_and_evidence():
    evidence = [Evidence("ev_1", "c1", "p1", "paper.pdf", 2, 2, "Results",
                         "Xylose gave furfural yield 78.5% at 180 C for 2 h.", 1.0)]
    result = extract_reaction_evidence(evidence)
    assert result["schema_version"] == EXTRACTION_JSON_SCHEMA["properties"]["schema_version"]["const"]
    assert any(x["field"] == "yield" and x["normalized_value"] == 78.5 and
               x["unit"] == "%" and x["evidence_id"] == "ev_1" for x in result["records"])
    assert any(x["field"] == "temperature" and x["unit"] == "°C" for x in result["records"])
    chemical = extract_reaction_evidence([
        Evidence("ev_2", "c2", "p1", "paper.pdf", 3, 3, "Methods",
                 "SnCl4 catalyst in EMIMBr solvent converted xylose to furfural.", 1.0)
    ])
    assert any(x["field"] == "catalyst" and x["raw_value"] == "sncl4" for x in chemical["records"])
    assert any(x["field"] == "solvent" and x["raw_value"] == "emimbr" for x in chemical["records"])


def test_optional_reranker_records_explainable_order():
    candidates = [
        Evidence("e1", "c1", "p", "p.pdf", 1, 1, "", "furfural only", 0.9),
        Evidence("e2", "c2", "p", "p.pdf", 2, 2, "", "xylose furfural yield", 0.5),
    ]
    result = LexicalCoverageReranker().rerank("xylose furfural yield", candidates)
    assert result[0].chunk_id == "c2"


def test_sqlite_backup_is_consistent_and_never_overwrites(tmp_path: Path):
    source = Store(tmp_path / "live.sqlite3")
    source.create_run("run_before", "req_before", "test", status="completed")
    destination = tmp_path / "backup.sqlite3"
    result = source.backup(destination)
    assert result["status"] == "completed" and result["size_bytes"] > 0

    import sqlite3
    with sqlite3.connect(destination) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    source.create_run("run_after", "req_after", "test", status="completed")
    with sqlite3.connect(destination) as backup:
        assert backup.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    try:
        source.backup(destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("backup must not overwrite an existing snapshot")
