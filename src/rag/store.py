from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import Chunk


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS papers (
  paper_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL UNIQUE, file_name TEXT NOT NULL,
  file_path TEXT NOT NULL, size_bytes INTEGER NOT NULL, status TEXT NOT NULL,
  page_count INTEGER NOT NULL DEFAULT 0, ocr_pages INTEGER NOT NULL DEFAULT 0,
  failed_pages_json TEXT NOT NULL DEFAULT '[]', parser_version TEXT NOT NULL,
  indexed_at TEXT, last_error_json TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  paper_name TEXT NOT NULL, page_start INTEGER NOT NULL, page_end INTEGER NOT NULL,
  section_path TEXT NOT NULL, text TEXT NOT NULL, token_count INTEGER NOT NULL,
  previous_chunk_id TEXT, next_chunk_id TEXT, parser_version TEXT NOT NULL,
  chunker_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);
CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  paper_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  dimensions INTEGER NOT NULL, vector BLOB NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_paper ON embeddings(paper_id);
CREATE TABLE IF NOT EXISTS attachments (
  attachment_id TEXT PRIMARY KEY, paper_id TEXT REFERENCES papers(paper_id), file_name TEXT NOT NULL,
  file_path TEXT NOT NULL, size_bytes INTEGER NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  stage TEXT, started_at TEXT NOT NULL, finished_at TEXT, counts_json TEXT NOT NULL DEFAULT '{}',
  error_json TEXT
);
CREATE TABLE IF NOT EXISTS run_documents (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE, paper_id TEXT,
  file_name TEXT NOT NULL, status TEXT NOT NULL, stage TEXT, counts_json TEXT NOT NULL DEFAULT '{}',
  error_json TEXT, PRIMARY KEY(run_id, file_name)
);
"""

MIGRATIONS = [
    (1, """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
  page_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL, text TEXT NOT NULL, extraction_method TEXT NOT NULL,
  ocr_confidence REAL, status TEXT NOT NULL, error_code TEXT, error_message TEXT,
  parser_version TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(paper_id, page_number)
);
CREATE INDEX IF NOT EXISTS idx_pages_paper ON pages(paper_id, page_number);
CREATE TABLE IF NOT EXISTS run_stages (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  stage TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
  started_at TEXT, finished_at TEXT, duration_ms REAL,
  input_count INTEGER, output_count INTEGER, component_version TEXT,
  error_json TEXT, PRIMARY KEY(run_id, stage, attempt)
);
CREATE TABLE IF NOT EXISTS query_runs (
  request_id TEXT PRIMARY KEY, status TEXT NOT NULL, question TEXT NOT NULL,
  normalized_query TEXT NOT NULL, paper_ids_json TEXT NOT NULL DEFAULT '[]',
  answer_mode TEXT, degraded INTEGER NOT NULL DEFAULT 0,
  insufficient_evidence INTEGER NOT NULL DEFAULT 0,
  corpus_incomplete INTEGER NOT NULL DEFAULT 0, warnings_json TEXT NOT NULL DEFAULT '[]',
  timings_json TEXT NOT NULL DEFAULT '{}', started_at TEXT NOT NULL, finished_at TEXT,
  error_json TEXT
);
CREATE TABLE IF NOT EXISTS query_candidates (
  request_id TEXT NOT NULL REFERENCES query_runs(request_id) ON DELETE CASCADE,
  chunk_id TEXT NOT NULL, retriever TEXT NOT NULL, rank INTEGER NOT NULL,
  score REAL NOT NULL, selected INTEGER NOT NULL DEFAULT 0,
  evidence_id TEXT, PRIMARY KEY(request_id, retriever, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_query_candidates_request ON query_candidates(request_id, retriever, rank);
"""),
    (2, """
CREATE TABLE IF NOT EXISTS ingestion_jobs (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
  file_path TEXT, force INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 1,
  parent_run_id TEXT, created_at TEXT NOT NULL, claimed_at TEXT
);
"""),
    (3, """
CREATE TABLE IF NOT EXISTS extraction_runs (
  extraction_run_id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES query_runs(request_id) ON DELETE CASCADE,
  status TEXT NOT NULL, schema_version TEXT NOT NULL, extractor_version TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, error_json TEXT
);
CREATE TABLE IF NOT EXISTS structured_records (
  record_id TEXT PRIMARY KEY, extraction_run_id TEXT NOT NULL REFERENCES extraction_runs(extraction_run_id) ON DELETE CASCADE,
  field_name TEXT NOT NULL, raw_value TEXT NOT NULL, normalized_value_json TEXT,
  unit TEXT, evidence_id TEXT NOT NULL, conflict_group TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structured_records_run ON structured_records(extraction_run_id,field_name);
"""),
    (4, """
ALTER TABLE ingestion_jobs ADD COLUMN source_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_source_hash ON ingestion_jobs(source_hash);
"""),
    (5, """
ALTER TABLE chunks ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chunks ADD COLUMN overlap_tokens INTEGER NOT NULL DEFAULT 64;
ALTER TABLE chunks ADD COLUMN chunk_reason TEXT NOT NULL DEFAULT 'structure_or_size_boundary';
UPDATE chunks SET ordinal=(SELECT COUNT(1)-1 FROM chunks prior
  WHERE prior.paper_id=chunks.paper_id AND prior.rowid<=chunks.rowid);
CREATE INDEX IF NOT EXISTS idx_chunks_position ON chunks(paper_id,ordinal);
"""),
    (6, """
ALTER TABLE query_runs ADD COLUMN query_tokens_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE query_runs ADD COLUMN expansions_json TEXT NOT NULL DEFAULT '[]';
"""),
    (7, """
ALTER TABLE query_runs ADD COLUMN generation_provider TEXT;
ALTER TABLE query_runs ADD COLUMN generation_model TEXT;
ALTER TABLE query_runs ADD COLUMN prompt_version TEXT;
"""),
    (8, """
ALTER TABLE query_candidates RENAME TO query_candidates_old;
CREATE TABLE query_candidates (
  request_id TEXT NOT NULL REFERENCES query_runs(request_id) ON DELETE CASCADE,
  chunk_id TEXT NOT NULL, retriever TEXT NOT NULL, rank INTEGER NOT NULL,
  score REAL NOT NULL, selected INTEGER NOT NULL DEFAULT 0, evidence_id TEXT,
  paper_id TEXT, paper_name TEXT, page_start INTEGER, page_end INTEGER,
  section_path TEXT, excerpt TEXT,
  PRIMARY KEY(request_id, retriever, chunk_id)
);
INSERT INTO query_candidates(request_id,chunk_id,retriever,rank,score,selected,evidence_id,
  paper_id,paper_name,page_start,page_end,section_path,excerpt)
SELECT q.request_id,q.chunk_id,q.retriever,q.rank,q.score,q.selected,q.evidence_id,
  c.paper_id,c.paper_name,c.page_start,c.page_end,c.section_path,substr(c.text,1,1600)
FROM query_candidates_old q LEFT JOIN chunks c ON c.chunk_id=q.chunk_id;
DROP TABLE query_candidates_old;
CREATE INDEX IF NOT EXISTS idx_query_candidates_request ON query_candidates(request_id,retriever,rank);
"""),
    (9, """
ALTER TABLE query_runs ADD COLUMN answer_text TEXT;
"""),
    (10, """
ALTER TABLE run_stages ADD COLUMN paper_id TEXT;
"""),
]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._write_lock = threading.Lock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version not in applied:
                    conn.executescript(sql)
                    conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)", (version, now()))

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def health(self) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1

    def backup(self, destination: Path | None = None) -> dict:
        """Create a transactionally consistent SQLite snapshot without overwriting files."""
        if destination is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            destination = self.path.parent / "backups" / f"rag-{stamp}.sqlite3"
        destination = destination.resolve()
        if destination == self.path.resolve():
            raise ValueError("backup destination must differ from the live database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"backup already exists: {destination}")
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.partial"
        try:
            with self._write_lock, closing(sqlite3.connect(self.path)) as source, \
                    closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
                target.commit()
            with closing(sqlite3.connect(temporary)) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("backup integrity check failed")
            if destination.exists():
                raise FileExistsError(f"backup already exists: {destination}")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {"status": "completed", "path": str(destination),
                "size_bytes": destination.stat().st_size, "created_at": now()}

    def create_run(self, run_id: str, request_id: str, kind: str, *, status: str = "running") -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO runs(run_id,request_id,kind,status,started_at) VALUES(?,?,?,?,?)",
                (run_id, request_id, kind, status, now()),
            )

    def create_ingestion_job(self, run_id: str, request_id: str, *, file_path: str | None,
                             force: bool, parent_run_id: str | None = None,
                             source_hash: str | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO runs(run_id,request_id,kind,status,started_at) VALUES(?,?,?,?,?)",
                (run_id, request_id, "ingestion", "queued", now()),
            )
            conn.execute(
                """INSERT INTO ingestion_jobs(run_id,file_path,force,parent_run_id,created_at,source_hash)
                   VALUES(?,?,?,?,?,?)""",
                (run_id, file_path, int(force), parent_run_id, now(), source_hash),
            )

    def active_ingestion_for_hash(self, source_hash: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("""SELECT r.run_id,r.request_id,r.status FROM ingestion_jobs j
                                  JOIN runs r ON r.run_id=j.run_id
                                  WHERE j.source_hash=? AND r.status IN ('queued','running')
                                  ORDER BY j.created_at LIMIT 1""", (source_hash,)).fetchone()
            return dict(row) if row else None

    def active_full_ingestion(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("""SELECT r.run_id,r.request_id,r.status FROM ingestion_jobs j
                                  JOIN runs r ON r.run_id=j.run_id WHERE j.file_path IS NULL
                                  AND r.status IN ('queued','running') ORDER BY j.created_at LIMIT 1""").fetchone()
            return dict(row) if row else None

    def claim_ingestion_job(self) -> dict | None:
        with self._write_lock, self.connect() as conn:
            row = conn.execute(
                """SELECT j.*,r.request_id FROM ingestion_jobs j JOIN runs r ON r.run_id=j.run_id
                   WHERE r.status='queued' ORDER BY j.created_at LIMIT 1"""
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE runs SET status='running' WHERE run_id=? AND status='queued'", (row["run_id"],))
            conn.execute("UPDATE ingestion_jobs SET claimed_at=? WHERE run_id=?", (now(), row["run_id"]))
            return dict(row)

    def recover_interrupted_runs(self) -> int:
        error = json.dumps({"error_code": "TASK_INTERRUPTED", "stage": "task",
                            "message": "服务重启时任务仍在运行；未将其伪装为成功",
                            "exception_type": "ServiceRestart", "retryable": True,
                            "traceback": ""}, ensure_ascii=False)
        with self._write_lock, self.connect() as conn:
            rows = conn.execute("SELECT run_id FROM runs WHERE status='running'").fetchall()
            for row in rows:
                conn.execute("UPDATE runs SET status='interrupted',finished_at=?,error_json=? WHERE run_id=?",
                             (now(), error, row["run_id"]))
                conn.execute("""UPDATE run_documents SET status='interrupted',error_json=?
                                WHERE run_id=? AND status='running'""", (error, row["run_id"]))
                conn.execute("""UPDATE run_stages SET status='interrupted',finished_at=?,error_json=?
                                WHERE run_id=? AND status='running'""", (now(), error, row["run_id"]))
            return len(rows)

    def job_for_run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ingestion_jobs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def update_run(self, run_id: str, *, status: str | None = None, stage: str | None = None,
                   counts: dict | None = None, error: dict | None = None, finished: bool = False) -> None:
        fields, values = [], []
        for key, value in (("status", status), ("stage", stage)):
            if value is not None:
                fields.append(f"{key}=?")
                values.append(value)
        if counts is not None:
            fields.append("counts_json=?")
            values.append(json.dumps(counts, ensure_ascii=False))
        if error is not None:
            fields.append("error_json=?")
            values.append(json.dumps(error, ensure_ascii=False))
        if finished:
            fields.append("finished_at=?")
            values.append(now())
        if not fields:
            return
        values.append(run_id)
        with self._write_lock, self.connect() as conn:
            conn.execute(f"UPDATE runs SET {','.join(fields)} WHERE run_id=?", values)

    def update_run_document(self, run_id: str, file_name: str, *, paper_id: str | None,
                            status: str, stage: str, counts: dict | None = None,
                            error: dict | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """INSERT INTO run_documents(run_id,paper_id,file_name,status,stage,counts_json,error_json)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,file_name) DO UPDATE SET
                   paper_id=excluded.paper_id,status=excluded.status,stage=excluded.stage,
                   counts_json=excluded.counts_json,error_json=excluded.error_json""",
                (run_id, paper_id, file_name, status, stage,
                 json.dumps(counts or {}, ensure_ascii=False),
                 json.dumps(error, ensure_ascii=False) if error else None),
            )

    def start_stage(self, run_id: str, stage: str, *, paper_id: str | None = None,
                    component_version: str | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            attempt = conn.execute(
                "SELECT COALESCE(MAX(attempt),0)+1 FROM run_stages WHERE run_id=? AND stage=?",
                (run_id, stage),
            ).fetchone()[0]
            conn.execute("""INSERT INTO run_stages(run_id,stage,attempt,status,started_at,
                            paper_id,component_version) VALUES(?,?,?,'running',?,?,?)""",
                         (run_id, stage, attempt, now(), paper_id, component_version))

    def finish_stage(self, run_id: str, stage: str, *, status: str, duration_ms: float,
                     output_count: int | None = None, error: dict | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("""UPDATE run_stages SET status=?,finished_at=?,duration_ms=?,
                            output_count=?,error_json=? WHERE run_id=? AND stage=? AND attempt=(
                            SELECT MAX(attempt) FROM run_stages WHERE run_id=? AND stage=?)""",
                         (status, now(), duration_ms, output_count,
                          json.dumps(error, ensure_ascii=False) if error else None,
                          run_id, stage, run_id, stage))

    def paper_by_hash(self, source_hash: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM papers WHERE source_hash=?", (source_hash,)).fetchone()
            return dict(row) if row else None

    def register_processing_paper(self, *, paper_id: str, source_hash: str, file_name: str,
                                  file_path: str, size_bytes: int,
                                  parser_version: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("""INSERT INTO papers(paper_id,source_hash,file_name,file_path,size_bytes,status,
                            parser_version) VALUES(?,?,?,?,?,'processing',?)
                            ON CONFLICT(paper_id) DO NOTHING""",
                         (paper_id, source_hash, file_name, file_path, size_bytes, parser_version))

    def mark_paper_failed(self, paper_id: str, error: dict) -> None:
        with self._write_lock, self.connect() as conn:
            row = conn.execute("SELECT status,page_count FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
            if not row:
                return
            # A failed rebuild must not destroy a previously usable corpus version.
            status = row["status"] if row["status"] in {"ready", "partial_failed"} and row["page_count"] else "failed"
            conn.execute("UPDATE papers SET status=?,last_error_json=? WHERE paper_id=?",
                         (status, json.dumps(error, ensure_ascii=False), paper_id))

    def replace_paper(self, paper: dict, chunks: list[Chunk], embeddings: list[list[float]] | None = None,
                      pages: list | None = None,
                      *, embedding_provider: str | None = None,
                      embedding_model: str | None = None) -> None:
        if not chunks:
            raise ValueError("refusing to mark paper ready with zero chunks")
        if embeddings is not None and len(embeddings) != len(chunks):
            raise ValueError("chunk and embedding counts differ")
        with self._write_lock, self.connect() as conn:
            # Keep the stable paper row so linked SI attachments remain valid. Query
            # candidates are immutable snapshots and deliberately survive re-indexing.
            conn.execute("DELETE FROM chunks WHERE paper_id=?", (paper["paper_id"],))
            conn.execute("DELETE FROM pages WHERE paper_id=?", (paper["paper_id"],))
            conn.execute(
                """INSERT INTO papers(paper_id,source_hash,file_name,file_path,size_bytes,status,page_count,
                   ocr_pages,failed_pages_json,parser_version,indexed_at,last_error_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)
                   ON CONFLICT(paper_id) DO UPDATE SET source_hash=excluded.source_hash,
                   file_name=excluded.file_name,file_path=excluded.file_path,size_bytes=excluded.size_bytes,
                   status=excluded.status,page_count=excluded.page_count,ocr_pages=excluded.ocr_pages,
                   failed_pages_json=excluded.failed_pages_json,parser_version=excluded.parser_version,
                   indexed_at=excluded.indexed_at,last_error_json=NULL""",
                (paper["paper_id"], paper["source_hash"], paper["file_name"], paper["file_path"],
                 paper["size_bytes"], paper.get("status", "ready"), paper["page_count"], paper["ocr_pages"],
                 json.dumps(paper["failed_pages"], ensure_ascii=False), paper["parser_version"], now()),
            )
            conn.executemany(
                """INSERT INTO chunks(chunk_id,paper_id,paper_name,page_start,page_end,section_path,text,
                   token_count,ordinal,overlap_tokens,chunk_reason,previous_chunk_id,next_chunk_id,
                   parser_version,chunker_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(c.chunk_id, c.paper_id, c.paper_name, c.page_start, c.page_end, c.section_path,
                  c.text, c.token_count, c.ordinal, c.overlap_tokens, c.chunk_reason,
                  c.previous_chunk_id, c.next_chunk_id,
                  c.parser_version, c.chunker_version) for c in chunks],
            )
            if pages:
                conn.executemany(
                    """INSERT INTO pages(page_id,paper_id,page_number,text,extraction_method,
                       ocr_confidence,status,error_code,error_message,parser_version,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    [(f"page_{paper['paper_id'][6:]}_{p.number:05d}", paper["paper_id"], p.number,
                      p.text, p.extraction_method, p.ocr_confidence, p.status, p.error_code,
                      p.error_message, p.parser_version, now()) for p in pages],
                )
            if embeddings is not None:
                import numpy as np
                conn.executemany(
                    """INSERT INTO embeddings(chunk_id,paper_id,provider,model,dimensions,vector,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    [(chunk.chunk_id, chunk.paper_id, embedding_provider, embedding_model, len(vector),
                      np.asarray(vector, dtype=np.float32).tobytes(), now())
                     for chunk, vector in zip(chunks, embeddings)],
                )

    def register_attachment(self, item: dict) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """INSERT INTO attachments(attachment_id,paper_id,file_name,file_path,size_bytes,status)
                   VALUES(?,?,?,?,?,'registered_not_parsed') ON CONFLICT(attachment_id) DO UPDATE SET
                   paper_id=excluded.paper_id,file_path=excluded.file_path,size_bytes=excluded.size_bytes""",
                (item["attachment_id"], item.get("paper_id"), item["file_name"],
                 item["file_path"], item["size_bytes"]),
            )

    def chunks(self, paper_ids: list[str] | None = None) -> list[dict]:
        with self.connect() as conn:
            if paper_ids:
                marks = ",".join("?" for _ in paper_ids)
                rows = conn.execute(f"SELECT * FROM chunks WHERE paper_id IN ({marks})", paper_ids).fetchall()
            else:
                rows = conn.execute("SELECT * FROM chunks").fetchall()
            return [dict(row) for row in rows]

    def chunks_with_embeddings(self, paper_ids: list[str] | None = None,
                               model: str | None = None) -> list[dict]:
        query = """SELECT c.*,e.provider embedding_provider,e.model embedding_model,
                   e.dimensions,e.vector FROM chunks c JOIN embeddings e ON e.chunk_id=c.chunk_id"""
        clauses: list[str] = []
        values: list[str] = []
        if paper_ids:
            clauses.append("c.paper_id IN (" + ",".join("?" for _ in paper_ids) + ")")
            values.extend(paper_ids)
        if model:
            clauses.append("e.model=?")
            values.append(model)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, values).fetchall()]

    def embedding_stats(self) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) count,COUNT(DISTINCT model) models FROM embeddings").fetchone()
            by_model = [dict(x) for x in conn.execute(
                "SELECT provider,model,dimensions,COUNT(*) count FROM embeddings GROUP BY provider,model,dimensions"
            ).fetchall()]
            return {"count": row["count"], "model_count": row["models"], "by_model": by_model}

    def replace_all_embeddings(self, chunks: list[dict], vectors: list[list[float]], *,
                               provider: str, model: str) -> None:
        if len(chunks) != len(vectors) or not chunks:
            raise ValueError("non-empty chunk and embedding counts must match")
        import numpy as np
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or next(iter(dimensions)) <= 0:
            raise ValueError("embedding dimensions are empty or inconsistent")
        with self._write_lock, self.connect() as conn:
            conn.execute("DELETE FROM embeddings")
            conn.executemany(
                """INSERT INTO embeddings(chunk_id,paper_id,provider,model,dimensions,vector,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                [(chunk["chunk_id"], chunk["paper_id"], provider, model, len(vector),
                  np.asarray(vector, dtype=np.float32).tobytes(), now())
                 for chunk, vector in zip(chunks, vectors)],
            )

    def papers(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM papers ORDER BY file_name").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["failed_pages"] = json.loads(item.pop("failed_pages_json"))
                raw_last_error = item.pop("last_error_json")
                item["last_error"] = json.loads(raw_last_error) if raw_last_error else None
                item["attachments"] = [dict(x) for x in conn.execute(
                    "SELECT * FROM attachments WHERE paper_id=? ORDER BY file_name", (item["paper_id"],)
                ).fetchall()]
                result.append(item)
            return result

    def paper(self, paper_id: str) -> dict | None:
        return next((item for item in self.papers() if item["paper_id"] == paper_id), None)

    def pages(self, paper_id: str) -> list[dict]:
        with self.connect() as conn:
            result = [dict(row) for row in conn.execute(
                "SELECT * FROM pages WHERE paper_id=? ORDER BY page_number", (paper_id,)
            ).fetchall()]
            for item in result:
                item["chunk_ids"] = [row[0] for row in conn.execute(
                    """SELECT chunk_id FROM chunks WHERE paper_id=? AND page_start<=?
                       AND page_end>=? ORDER BY ordinal""",
                    (paper_id, item["page_number"], item["page_number"]),
                ).fetchall()]
            return result

    def page(self, paper_id: str, page_number: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM pages WHERE paper_id=? AND page_number=?", (paper_id, page_number)
            ).fetchone()
            return dict(row) if row else None

    def paper_chunks(self, paper_id: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM chunks WHERE paper_id=? ORDER BY ordinal LIMIT ? OFFSET ?",
                (paper_id, limit, offset),
            ).fetchall()]

    def chunk(self, chunk_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
            return dict(row) if row else None

    def run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["counts"] = json.loads(item.pop("counts_json"))
            raw_error = item.pop("error_json")
            item["error"] = json.loads(raw_error) if raw_error else None
            item["documents"] = []
            for doc in conn.execute("SELECT * FROM run_documents WHERE run_id=? ORDER BY file_name", (run_id,)):
                value = dict(doc)
                value["counts"] = json.loads(value.pop("counts_json"))
                raw_doc_error = value.pop("error_json")
                value["error"] = json.loads(raw_doc_error) if raw_doc_error else None
                item["documents"].append(value)
            item["stages"] = []
            for stage_row in conn.execute(
                "SELECT * FROM run_stages WHERE run_id=? ORDER BY started_at,attempt", (run_id,)
            ):
                value = dict(stage_row)
                raw_stage_error = value.pop("error_json")
                value["error"] = json.loads(raw_stage_error) if raw_stage_error else None
                item["stages"].append(value)
            job = conn.execute("SELECT * FROM ingestion_jobs WHERE run_id=?", (run_id,)).fetchone()
            item["job"] = dict(job) if job else None
            return item

    def latest_runs(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT r.*,EXISTS(SELECT 1 FROM ingestion_jobs j
                                WHERE j.run_id=r.run_id) retryable_job
                                FROM runs r ORDER BY r.started_at DESC LIMIT ?""", (limit,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["counts"] = json.loads(item.pop("counts_json"))
                raw_error = item.pop("error_json")
                item["error"] = json.loads(raw_error) if raw_error else None
                result.append(item)
            return result

    def begin_query(self, request_id: str, question: str, normalized_query: str,
                    paper_ids: list[str] | None, *, query_tokens: list[str] | None = None,
                    expansions: list[str] | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """INSERT INTO query_runs(request_id,status,question,normalized_query,
                   paper_ids_json,started_at,query_tokens_json,expansions_json) VALUES(?,?,?,?,?,?,?,?)""",
                (request_id, "running", question, normalized_query,
                 json.dumps(paper_ids or [], ensure_ascii=False), now(),
                 json.dumps(query_tokens or [], ensure_ascii=False),
                 json.dumps(expansions or [], ensure_ascii=False)),
            )

    def save_query_candidates(self, request_id: str, retriever: str, candidates: list,
                              selected_ids: set[str] | None = None) -> None:
        selected_ids = selected_ids or set()
        with self._write_lock, self.connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO query_candidates(request_id,chunk_id,retriever,rank,
                   score,selected,evidence_id,paper_id,paper_name,page_start,page_end,section_path,excerpt)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(request_id, item.chunk_id, retriever, rank, item.score,
                  int(item.chunk_id in selected_ids), item.evidence_id, item.paper_id,
                  item.paper_name, item.page_start, item.page_end, item.section_path,
                  item.excerpt[:1600])
                 for rank, item in enumerate(candidates, start=1)],
            )

    def finish_query(self, request_id: str, *, status: str, answer_mode: str,
                     degraded: bool, insufficient_evidence: bool, corpus_incomplete: bool,
                     warnings: list[dict], timings: dict, error: dict | None = None,
                     generation_provider: str | None = None,
                     generation_model: str | None = None,
                     prompt_version: str | None = None,
                     answer_text: str | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """UPDATE query_runs SET status=?,answer_mode=?,degraded=?,
                   insufficient_evidence=?,corpus_incomplete=?,warnings_json=?,timings_json=?,
                   finished_at=?,error_json=?,generation_provider=?,generation_model=?,
                   prompt_version=?,answer_text=? WHERE request_id=?""",
                (status, answer_mode, int(degraded), int(insufficient_evidence),
                 int(corpus_incomplete), json.dumps(warnings, ensure_ascii=False),
                 json.dumps(timings, ensure_ascii=False), now(),
                 json.dumps(error, ensure_ascii=False) if error else None,
                 generation_provider, generation_model, prompt_version, answer_text, request_id),
            )

    def query_diagnostic(self, request_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM query_runs WHERE request_id=?", (request_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            for source, target in (("paper_ids_json", "paper_ids"),
                                   ("warnings_json", "warnings"), ("timings_json", "timings"),
                                   ("query_tokens_json", "query_tokens"),
                                   ("expansions_json", "expansions")):
                item[target] = json.loads(item.pop(source))
            raw_error = item.pop("error_json")
            item["error"] = json.loads(raw_error) if raw_error else None
            item["candidates"] = [dict(value) for value in conn.execute(
                """SELECT * FROM query_candidates WHERE request_id=?
                   ORDER BY retriever,rank""", (request_id,)
            ).fetchall()]
            item["extractions"] = [dict(value) for value in conn.execute(
                "SELECT * FROM extraction_runs WHERE request_id=? ORDER BY started_at", (request_id,)
            ).fetchall()]
            return item

    def save_extraction(self, extraction_run_id: str, request_id: str, payload: dict,
                        *, extractor_version: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("""INSERT INTO extraction_runs(extraction_run_id,request_id,status,schema_version,
                            extractor_version,started_at,finished_at) VALUES(?,?,?,?,?,?,?)""",
                         (extraction_run_id, request_id, "completed", payload["schema_version"],
                          extractor_version, now(), now()))
            conn.executemany("""INSERT INTO structured_records(record_id,extraction_run_id,field_name,
                                raw_value,normalized_value_json,unit,evidence_id,conflict_group,created_at)
                                VALUES(?,?,?,?,?,?,?,?,?)""",
                             [(f"rec_{extraction_run_id[4:]}_{index:04d}", extraction_run_id,
                               value["field"], value["raw_value"],
                               json.dumps(value.get("normalized_value"), ensure_ascii=False),
                               value.get("unit"), value["evidence_id"], None, now())
                              for index, value in enumerate(payload["records"])])

    def extraction(self, extraction_run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM extraction_runs WHERE extraction_run_id=?",
                               (extraction_run_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["records"] = []
            for value in conn.execute("SELECT * FROM structured_records WHERE extraction_run_id=? ORDER BY record_id",
                                      (extraction_run_id,)):
                record = dict(value)
                record["normalized_value"] = json.loads(record.pop("normalized_value_json"))
                item["records"].append(record)
            return item
