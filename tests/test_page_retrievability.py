from pathlib import Path
from dataclasses import replace
import struct

import fitz

from rag.config import Settings
from rag.pipeline import Pipeline


def _settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    var = tmp_path / "var"
    return Settings(project_dir=tmp_path, data_dir=data, var_dir=var, db_path=var / "rag.sqlite3",
                    log_path=var / "rag.jsonl", debug=False, ocr_enabled=False, min_page_chars=10,
                    chunk_tokens=20, chunk_overlap=0, chat_provider="extractive", chat_base_url=None,
                    chat_api_key=None, chat_model=None, chat_timeout_seconds=5,
                    embedding_provider="disabled")


def _pdf(path: Path) -> None:
    doc = fitz.open()
    for text in ("first safe page " * 20, "second safe page " * 20):
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_partial_paper_filters_bad_and_cross_page_chunks_but_keeps_safe_chunks(tmp_path):
    cfg = _settings(tmp_path)
    path = cfg.data_dir / "paper.pdf"
    _pdf(path)
    pipeline = Pipeline(cfg)
    assert pipeline.ingest()["status"] == "completed"
    paper = pipeline.store.papers()[0]
    with pipeline.store.connect() as conn:
        conn.execute("UPDATE papers SET status='partial_failed' WHERE paper_id=?", (paper["paper_id"],))
        conn.execute("UPDATE collection_documents SET status='partial_failed' WHERE paper_id=?", (paper["paper_id"],))
        conn.execute("UPDATE pages SET status='partial_failed',retrievable=0 WHERE paper_id=? AND page_number=2",
                     (paper["paper_id"],))
        conn.execute("UPDATE chunks SET page_end=2 WHERE paper_id=? AND ordinal=0", (paper["paper_id"],))
        conn.execute("UPDATE chunks SET page_end=1 WHERE paper_id=? AND ordinal=1", (paper["paper_id"],))
        conn.executemany("""INSERT INTO embeddings(chunk_id,paper_id,provider,model,dimensions,vector,created_at)
                            VALUES(?,?,?, ?,?,?,CURRENT_TIMESTAMP)""",
                         [(row["chunk_id"], paper["paper_id"], "test", "test-model", 1,
                           struct.pack("f", 1.0))
                          for row in conn.execute("SELECT chunk_id FROM chunks WHERE paper_id=?", (paper["paper_id"],))])
    lexical = pipeline.store.scoped_chunks("default", "anonymous", include_official=True)
    dense = pipeline.store.scoped_chunks("default", "anonymous", include_official=True,
                                         with_embeddings=True, embedding_model="test-model")
    assert lexical
    assert all(item["page_start"] == item["page_end"] == 1 for item in lexical)
    assert {item["chunk_id"] for item in dense} == {item["chunk_id"] for item in lexical}
    with pipeline.store.connect() as conn:
        conn.execute("UPDATE pages SET text=text || ?,retrievable=0 WHERE paper_id=? AND page_number=1",
                     ("\ue100", paper["paper_id"]))
    assert pipeline.store.scoped_chunks("default", "anonymous", include_official=True) == []
