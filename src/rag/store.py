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
    (11, """
CREATE TABLE IF NOT EXISTS principals (
  tenant_id TEXT NOT NULL, subject TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(tenant_id, subject)
);
CREATE TABLE IF NOT EXISTS collections (
  collection_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
  scope_type TEXT NOT NULL CHECK(scope_type IN ('official','private','temporary')),
  owner_id TEXT, conversation_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK((scope_type='official' AND owner_id IS NULL AND conversation_id IS NULL) OR
        (scope_type='private' AND owner_id IS NOT NULL AND conversation_id IS NULL) OR
        (scope_type='temporary' AND owner_id IS NOT NULL AND conversation_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_collections_access ON collections(tenant_id,scope_type,owner_id);
CREATE TABLE IF NOT EXISTS conversations (
  conversation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, owner_id TEXT NOT NULL,
  title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(tenant_id,owner_id,updated_at);
CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
  request_id TEXT, original_question TEXT, retrieval_question TEXT,
  citations_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id,created_at);
CREATE TABLE IF NOT EXISTS collection_documents (
  document_id TEXT PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
  paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE RESTRICT,
  tenant_id TEXT NOT NULL, owner_id TEXT, scope_type TEXT NOT NULL,
  conversation_id TEXT, expires_at TEXT, status TEXT NOT NULL DEFAULT 'ready',
  source_name TEXT NOT NULL, stored_path TEXT, created_at TEXT NOT NULL,
  UNIQUE(collection_id,paper_id)
);
CREATE INDEX IF NOT EXISTS idx_collection_documents_scope ON collection_documents(tenant_id,scope_type,owner_id,conversation_id,expires_at);
CREATE INDEX IF NOT EXISTS idx_collection_documents_paper ON collection_documents(paper_id);
CREATE TABLE IF NOT EXISTS cleanup_events (
  cleanup_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, status TEXT NOT NULL,
  deleted_path INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}'
);
INSERT OR IGNORE INTO collections(collection_id,tenant_id,name,scope_type,owner_id,conversation_id,created_at,updated_at)
VALUES('col_official_default','default','平台官方知识库','official',NULL,NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO collection_documents(document_id,collection_id,paper_id,tenant_id,owner_id,scope_type,
  conversation_id,expires_at,status,source_name,stored_path,created_at)
SELECT 'doc_' || paper_id,'col_official_default',paper_id,'default',NULL,'official',NULL,NULL,status,file_name,file_path,CURRENT_TIMESTAMP
FROM papers;
"""),
    (12, """
ALTER TABLE ingestion_jobs ADD COLUMN tenant_id TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN owner_id TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN collection_id TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN scope_type TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN conversation_id TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN expires_at TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN document_id TEXT;
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_scope ON ingestion_jobs(tenant_id,owner_id,collection_id);
"""),
    (13, """
ALTER TABLE query_runs ADD COLUMN tenant_id TEXT;
ALTER TABLE query_runs ADD COLUMN subject TEXT;
ALTER TABLE query_runs ADD COLUMN collection_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE query_runs ADD COLUMN conversation_id TEXT;
ALTER TABLE query_runs ADD COLUMN include_official INTEGER NOT NULL DEFAULT 1;
ALTER TABLE query_runs ADD COLUMN retrieval_question TEXT;
CREATE INDEX IF NOT EXISTS idx_query_runs_scope ON query_runs(tenant_id,subject,conversation_id,started_at);
"""),
    (14, """
ALTER TABLE query_candidates ADD COLUMN document_id TEXT;
ALTER TABLE query_candidates ADD COLUMN collection_id TEXT;
ALTER TABLE query_candidates ADD COLUMN scope_type TEXT;
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
                             source_hash: str | None = None,
                             tenant_id: str | None = None, owner_id: str | None = None,
                             collection_id: str | None = None, scope_type: str | None = None,
                             conversation_id: str | None = None, expires_at: str | None = None,
                             document_id: str | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO runs(run_id,request_id,kind,status,started_at) VALUES(?,?,?,?,?)",
                (run_id, request_id, "ingestion", "queued", now()),
            )
            conn.execute(
                """INSERT INTO ingestion_jobs(run_id,file_path,force,parent_run_id,created_at,source_hash,
                   tenant_id,owner_id,collection_id,scope_type,conversation_id,expires_at,document_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, file_path, int(force), parent_run_id, now(), source_hash, tenant_id,
                 owner_id, collection_id, scope_type, conversation_id, expires_at, document_id),
            )

    def active_ingestion_for_hash(self, source_hash: str,
                                  collection_id: str | None = None) -> dict | None:
        with self.connect() as conn:
            sql = """SELECT r.run_id,r.request_id,r.status FROM ingestion_jobs j
                                  JOIN runs r ON r.run_id=j.run_id
                                  WHERE j.source_hash=? AND r.status IN ('queued','running')
                               """
            params: list[object] = [source_hash]
            if collection_id is not None:
                sql += " AND j.collection_id=?"
                params.append(collection_id)
            row = conn.execute(sql + " ORDER BY j.created_at LIMIT 1", params).fetchone()
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
                      embedding_model: str | None = None,
                      document_scope: dict | None = None) -> None:
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
            scope = document_scope or {
                "document_id": f"doc_{paper['paper_id']}", "collection_id": "col_official_default",
                "tenant_id": "default", "owner_id": None, "scope_type": "official",
                "conversation_id": None, "expires_at": None,
            }
            conn.execute(
                """INSERT INTO collection_documents(document_id,collection_id,paper_id,tenant_id,
                   owner_id,scope_type,conversation_id,expires_at,status,source_name,stored_path,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(collection_id,paper_id) DO UPDATE SET status=excluded.status,
                   source_name=excluded.source_name,stored_path=excluded.stored_path,
                   expires_at=excluded.expires_at""",
                (scope["document_id"], scope["collection_id"], paper["paper_id"], scope["tenant_id"],
                 scope.get("owner_id"), scope["scope_type"], scope.get("conversation_id"),
                 scope.get("expires_at"), paper.get("status", "ready"), paper["file_name"],
                 paper["file_path"], now()),
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
                    expansions: list[str] | None = None, tenant_id: str | None = None,
                    subject: str | None = None, collection_ids: list[str] | None = None,
                    conversation_id: str | None = None, include_official: bool = True,
                    retrieval_question: str | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """INSERT INTO query_runs(request_id,status,question,normalized_query,
                   paper_ids_json,started_at,query_tokens_json,expansions_json,tenant_id,subject,
                   collection_ids_json,conversation_id,include_official,retrieval_question)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (request_id, "running", question, normalized_query,
                 json.dumps(paper_ids or [], ensure_ascii=False), now(),
                 json.dumps(query_tokens or [], ensure_ascii=False),
                 json.dumps(expansions or [], ensure_ascii=False), tenant_id, subject,
                 json.dumps(collection_ids or [], ensure_ascii=False), conversation_id,
                 int(include_official), retrieval_question or question),
            )

    def save_query_candidates(self, request_id: str, retriever: str, candidates: list,
                              selected_ids: set[str] | None = None) -> None:
        selected_ids = selected_ids or set()
        with self._write_lock, self.connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO query_candidates(request_id,chunk_id,retriever,rank,
                   score,selected,evidence_id,paper_id,paper_name,page_start,page_end,section_path,excerpt,
                   document_id,collection_id,scope_type)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(request_id, item.chunk_id, retriever, rank, item.score,
                  int(item.chunk_id in selected_ids), item.evidence_id, item.paper_id,
                  item.paper_name, item.page_start, item.page_end, item.section_path,
                  item.excerpt[:1600], item.document_id, item.collection_id, item.source_type)
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
                                   ("collection_ids_json", "collection_ids"),
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

    def latest_query_runs(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT request_id,status,answer_mode,degraded,insufficient_evidence,
                          corpus_incomplete,timings_json,started_at,finished_at,error_json
                   FROM query_runs ORDER BY started_at DESC LIMIT ?""", (limit,)
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["timings"] = json.loads(item.pop("timings_json"))
            raw_error = item.pop("error_json")
            item["error"] = json.loads(raw_error) if raw_error else None
            items.append(item)
        return items

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

    # Multi-scope application model. Every method takes the verified tenant and
    # subject explicitly so API handlers cannot accidentally perform an
    # unscoped list/get/update.
    def ensure_principal(self, tenant_id: str, subject: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO principals VALUES(?,?,?)", (tenant_id, subject, now()))

    def accessible_collections(self, tenant_id: str, subject: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM collections WHERE tenant_id=? AND
                   (scope_type='official' OR owner_id=?) ORDER BY scope_type,name""",
                (tenant_id, subject),
            ).fetchall()
            return [dict(row) for row in rows]

    def pending_scope_documents(self, tenant_id: str, subject: str, *,
                                collection_ids: list[str] | None = None,
                                conversation_id: str | None = None,
                                include_official: bool = True) -> list[dict]:
        accessible = self.accessible_collections(tenant_id, subject)
        selected = set(collection_ids or [])
        target_ids = {
            item["collection_id"] for item in accessible
            if (include_official and item["scope_type"] == "official")
            or (item["scope_type"] == "private" and item["collection_id"] in selected)
            or (item["scope_type"] == "temporary" and item["conversation_id"] == conversation_id)
        }
        pending: list[dict] = []
        for collection_id in target_ids:
            for item in self.collection_documents(collection_id, tenant_id, subject) or []:
                if item["status"] not in {"ready", "completed"}:
                    pending.append({"document_id": item["document_id"],
                                    "source_name": item["source_name"], "status": item["status"]})
        return pending

    def link_document(self, *, document_id: str, collection_id: str, paper_id: str,
                      tenant_id: str, owner_id: str | None, scope_type: str,
                      source_name: str, stored_path: str | None,
                      conversation_id: str | None = None, expires_at: str | None = None,
                      status: str = "ready") -> dict:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """INSERT INTO collection_documents(document_id,collection_id,paper_id,tenant_id,
                   owner_id,scope_type,conversation_id,expires_at,status,source_name,stored_path,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(collection_id,paper_id) DO UPDATE SET status=excluded.status,
                   source_name=excluded.source_name,stored_path=COALESCE(excluded.stored_path,stored_path),
                   expires_at=excluded.expires_at""",
                (document_id, collection_id, paper_id, tenant_id, owner_id, scope_type,
                 conversation_id, expires_at, status, source_name, stored_path, now()),
            )
            row = conn.execute(
                "SELECT * FROM collection_documents WHERE collection_id=? AND paper_id=?",
                (collection_id, paper_id),
            ).fetchone()
            return dict(row)

    def collection_documents(self, collection_id: str, tenant_id: str, subject: str,
                             *, query: str | None = None) -> list[dict] | None:
        collection = self.collection_for_owner(collection_id, tenant_id, subject)
        if not collection:
            return None
        params: list[object] = [collection_id]
        sql = """SELECT d.document_id,d.collection_id,d.paper_id,d.scope_type,d.conversation_id,
                        d.expires_at,d.status,d.source_name,d.created_at,p.page_count,p.ocr_pages,
                        p.failed_pages_json
                 FROM collection_documents d JOIN papers p ON p.paper_id=d.paper_id
                 WHERE d.collection_id=?"""
        if query:
            sql += " AND lower(d.source_name) LIKE ? ESCAPE '\\'"
            escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        sql += " ORDER BY d.created_at DESC"
        with self.connect() as conn:
            items = [dict(row) for row in conn.execute(sql, params)]
            pending = [dict(row) for row in conn.execute(
                """SELECT j.document_id,j.collection_id,j.expires_at,r.status,j.created_at,j.file_path
                   FROM ingestion_jobs j JOIN runs r ON r.run_id=j.run_id
                   WHERE j.collection_id=? AND j.document_id IS NOT NULL
                     AND NOT EXISTS(SELECT 1 FROM collection_documents d WHERE d.document_id=j.document_id)
                   ORDER BY j.created_at DESC""", (collection_id,)
            )]
        for item in items:
            item["failed_pages"] = json.loads(item.pop("failed_pages_json"))
        for item in pending:
            item.update({"paper_id": None, "scope_type": collection["scope_type"],
                         "conversation_id": collection.get("conversation_id"),
                         "source_name": Path(item.pop("file_path")).name,
                         "page_count": 0, "ocr_pages": 0, "failed_pages": []})
        items = pending + items
        if query:
            items = [item for item in items if query.casefold() in item["source_name"].casefold()]
        return items

    def official_papers(self) -> list[dict]:
        official_ids: set[str]
        with self.connect() as conn:
            official_ids = {row[0] for row in conn.execute(
                "SELECT paper_id FROM collection_documents WHERE scope_type='official'"
            )}
        return [item for item in self.papers() if item["paper_id"] in official_ids]

    def official_paper(self, paper_id: str) -> dict | None:
        with self.connect() as conn:
            allowed = conn.execute(
                "SELECT 1 FROM collection_documents WHERE paper_id=? AND scope_type='official'",
                (paper_id,),
            ).fetchone()
        return self.paper(paper_id) if allowed else None

    def document_for_user(self, document_id: str, tenant_id: str, subject: str,
                          *, conversation_id: str | None = None) -> dict | None:
        params: list[object] = [document_id, tenant_id, subject]
        sql = """SELECT d.*,p.file_name,p.status AS paper_status,p.page_count,p.ocr_pages
                 FROM collection_documents d JOIN papers p ON p.paper_id=d.paper_id
                 WHERE d.document_id=? AND d.tenant_id=?
                   AND (d.scope_type='official' OR d.owner_id=?)"""
        if conversation_id is not None:
            sql += " AND d.conversation_id=?"
            params.append(conversation_id)
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def document_status(self, document_id: str, tenant_id: str, subject: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT j.document_id,j.collection_id,j.conversation_id,j.expires_at,
                          r.run_id,r.request_id,r.status,r.stage,r.started_at,r.finished_at,r.error_json
                   FROM ingestion_jobs j JOIN runs r ON r.run_id=j.run_id
                   WHERE j.document_id=? AND j.tenant_id=? AND j.owner_id=?""",
                (document_id, tenant_id, subject),
            ).fetchone()
            if row:
                item = dict(row)
                raw_error = item.pop("error_json")
                error = json.loads(raw_error) if raw_error else None
                item["error"] = ({key: error.get(key) for key in
                                  ("error_code", "stage", "message", "retryable")}
                                 if error else None)
                return item
        document = self.document_for_user(document_id, tenant_id, subject)
        if not document:
            return None
        return {key: document.get(key) for key in
                ("document_id", "collection_id", "conversation_id", "expires_at", "status")}

    def evidence_for_user(self, evidence_id: str, tenant_id: str, subject: str,
                          *, conversation_id: str | None = None) -> dict | None:
        conditions = ["d.scope_type='official'", "(d.scope_type='private' AND d.owner_id=?)"]
        params: list[object] = [subject]
        if conversation_id:
            conditions.append("(d.scope_type='temporary' AND d.owner_id=? AND d.conversation_id=?)")
            params.extend([subject, conversation_id])
        sql = f"""SELECT qc.evidence_id,qc.chunk_id,qc.paper_id,qc.paper_name,qc.page_start,
                         qc.page_end,qc.section_path,qc.excerpt,qc.score,d.document_id,
                         d.collection_id,d.scope_type,d.conversation_id
                  FROM query_candidates qc
                  JOIN query_runs qr ON qr.request_id=qc.request_id
                  JOIN collection_documents d
                    ON (qc.document_id IS NOT NULL AND d.document_id=qc.document_id)
                    OR (qc.document_id IS NULL AND d.paper_id=qc.paper_id)
                  WHERE qc.evidence_id=? AND qc.selected=1 AND d.tenant_id=?
                    AND (d.expires_at IS NULL OR d.expires_at>?)
                    AND ({' OR '.join(conditions)})
                  ORDER BY (qc.document_id IS NOT NULL AND d.document_id=qc.document_id) DESC,
                           qr.started_at DESC LIMIT 1"""
        params = [evidence_id, tenant_id, now(), *params]
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def set_document_status(self, document_id: str, status: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("UPDATE collection_documents SET status=? WHERE document_id=?",
                         (status, document_id))

    def expired_temporary_documents(self, *, at: str | None = None) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT * FROM collection_documents WHERE scope_type='temporary'
                   AND expires_at IS NOT NULL AND expires_at<=? ORDER BY expires_at""",
                (at or now(),),
            )]

    def unlink_document(self, document_id: str, tenant_id: str | None = None,
                        subject: str | None = None) -> dict | None:
        """Delete one logical document and physical rows only when unreferenced."""
        with self._write_lock, self.connect() as conn:
            sql = "SELECT * FROM collection_documents WHERE document_id=?"
            params: list[object] = [document_id]
            if tenant_id is not None and subject is not None:
                sql += " AND tenant_id=? AND owner_id=? AND scope_type IN ('private','temporary')"
                params.extend([tenant_id, subject])
            row = conn.execute(sql, params).fetchone()
            if not row:
                return None
            item = dict(row)
            conn.execute("DELETE FROM ingestion_jobs WHERE document_id=?", (document_id,))
            conn.execute("DELETE FROM collection_documents WHERE document_id=?", (document_id,))
            references = conn.execute(
                "SELECT count(1) FROM collection_documents WHERE paper_id=?", (item["paper_id"],)
            ).fetchone()[0]
            item["physical_deleted"] = references == 0
            if not references:
                conn.execute("DELETE FROM papers WHERE paper_id=?", (item["paper_id"],))
            return item

    def record_cleanup(self, document_id: str, status: str, *, deleted_path: bool,
                       detail: dict | None = None) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO cleanup_events VALUES(?,?,?,?,?,?)",
                (f"cleanup_{uuid.uuid4().hex}", document_id, status, int(deleted_path),
                 now(), json.dumps(detail or {}, ensure_ascii=False)),
            )

    def create_private_collection(self, tenant_id: str, subject: str, name: str) -> dict:
        collection_id = f"col_{uuid.uuid4().hex}"
        timestamp = now()
        with self._write_lock, self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO principals VALUES(?,?,?)", (tenant_id, subject, timestamp))
            conn.execute(
                """INSERT INTO collections(collection_id,tenant_id,name,scope_type,owner_id,
                   conversation_id,created_at,updated_at) VALUES(?,?,?,'private',?,NULL,?,?)""",
                (collection_id, tenant_id, name, subject, timestamp, timestamp),
            )
        return self.collection_for_owner(collection_id, tenant_id, subject) or {}

    def collection_for_owner(self, collection_id: str, tenant_id: str, subject: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM collections WHERE collection_id=? AND tenant_id=? AND
                   (scope_type='official' OR owner_id=?)""", (collection_id, tenant_id, subject)
            ).fetchone()
            return dict(row) if row else None

    def rename_private_collection(self, collection_id: str, tenant_id: str, subject: str, name: str) -> bool:
        with self._write_lock, self.connect() as conn:
            cursor = conn.execute(
                """UPDATE collections SET name=?,updated_at=? WHERE collection_id=? AND tenant_id=?
                   AND owner_id=? AND scope_type='private'""",
                (name, now(), collection_id, tenant_id, subject),
            )
            return cursor.rowcount == 1

    def delete_private_collection(self, collection_id: str, tenant_id: str, subject: str) -> bool:
        with self._write_lock, self.connect() as conn:
            cursor = conn.execute(
                """DELETE FROM collections WHERE collection_id=? AND tenant_id=?
                   AND owner_id=? AND scope_type='private'""", (collection_id, tenant_id, subject)
            )
            return cursor.rowcount == 1

    def create_conversation(self, tenant_id: str, subject: str, title: str) -> dict:
        conversation_id = f"conv_{uuid.uuid4().hex}"
        collection_id = f"col_temp_{conversation_id[5:]}"
        timestamp = now()
        with self._write_lock, self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO principals VALUES(?,?,?)", (tenant_id, subject, timestamp))
            conn.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?)",
                         (conversation_id, tenant_id, subject, title, timestamp, timestamp))
            conn.execute(
                """INSERT INTO collections(collection_id,tenant_id,name,scope_type,owner_id,
                   conversation_id,created_at,updated_at) VALUES(?,?,?,'temporary',?,?,?,?)""",
                (collection_id, tenant_id, f"会话临时资料：{title}", subject,
                 conversation_id, timestamp, timestamp),
            )
        return self.conversation(conversation_id, tenant_id, subject) or {}

    def conversations(self, tenant_id: str, subject: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT * FROM conversations WHERE tenant_id=? AND owner_id=?
                   ORDER BY updated_at DESC""", (tenant_id, subject)
            )]

    def conversation(self, conversation_id: str, tenant_id: str, subject: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id=? AND tenant_id=? AND owner_id=?",
                (conversation_id, tenant_id, subject),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["messages"] = [dict(message) for message in conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,message_id",
                (conversation_id,),
            )]
            for message in item["messages"]:
                message["citations"] = json.loads(message.pop("citations_json"))
            return item

    def rename_conversation(self, conversation_id: str, tenant_id: str, subject: str, title: str) -> bool:
        with self._write_lock, self.connect() as conn:
            cursor = conn.execute(
                """UPDATE conversations SET title=?,updated_at=? WHERE conversation_id=?
                   AND tenant_id=? AND owner_id=?""", (title, now(), conversation_id, tenant_id, subject)
            )
            return cursor.rowcount == 1

    def delete_conversation(self, conversation_id: str, tenant_id: str, subject: str) -> bool:
        with self._write_lock, self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE conversation_id=? AND tenant_id=? AND owner_id=?",
                (conversation_id, tenant_id, subject),
            )
            return cursor.rowcount == 1

    def add_message(self, conversation_id: str, tenant_id: str, subject: str, role: str,
                    content: str, *, request_id: str | None = None,
                    original_question: str | None = None,
                    retrieval_question: str | None = None,
                    citations: list[dict] | None = None) -> dict | None:
        message_id = f"msg_{uuid.uuid4().hex}"
        timestamp = now()
        with self._write_lock, self.connect() as conn:
            allowed = conn.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=? AND tenant_id=? AND owner_id=?",
                (conversation_id, tenant_id, subject),
            ).fetchone()
            if not allowed:
                return None
            conn.execute(
                """INSERT INTO messages(message_id,conversation_id,role,content,request_id,
                   original_question,retrieval_question,citations_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (message_id, conversation_id, role, content, request_id, original_question,
                 retrieval_question, json.dumps(citations or [], ensure_ascii=False), timestamp),
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE conversation_id=?",
                         (timestamp, conversation_id))
        return {"message_id": message_id, "role": role, "content": content,
                "request_id": request_id, "citations": citations or [], "created_at": timestamp}

    def scoped_chunks(self, tenant_id: str, subject: str, *,
                      collection_ids: list[str] | None = None,
                      conversation_id: str | None = None,
                      include_official: bool = True,
                      paper_ids: list[str] | None = None,
                      with_embeddings: bool = False,
                      embedding_model: str | None = None) -> list[dict]:
        """Return only authorized chunks; filtering happens in SQLite before retrieval."""
        selected = list(dict.fromkeys(collection_ids or []))
        clauses: list[str] = []
        params: list[object] = [tenant_id]
        if include_official:
            clauses.append("d.scope_type='official'")
        if selected:
            marks = ",".join("?" for _ in selected)
            clauses.append(f"(d.scope_type='private' AND d.owner_id=? AND d.collection_id IN ({marks}))")
            params.extend([subject, *selected])
        if conversation_id:
            clauses.append("(d.scope_type='temporary' AND d.owner_id=? AND d.conversation_id=?)")
            params.extend([subject, conversation_id])
        if not clauses:
            return []
        embedding_join = "JOIN embeddings e ON e.chunk_id=c.chunk_id" if with_embeddings else ""
        embedding_columns = ",e.provider,e.model,e.dimensions,e.vector" if with_embeddings else ""
        embedding_filter = " AND e.model=?" if with_embeddings and embedding_model else ""
        if embedding_filter:
            params.append(embedding_model)
        paper_filter = ""
        if paper_ids:
            paper_filter = f" AND c.paper_id IN ({','.join('?' for _ in paper_ids)})"
            params.extend(paper_ids)
        sql = f"""SELECT c.*,d.document_id,d.collection_id,d.scope_type,d.conversation_id,d.expires_at
                  {embedding_columns} FROM collection_documents d
                  JOIN chunks c ON c.paper_id=d.paper_id {embedding_join}
                  WHERE d.tenant_id=? AND d.status='ready'
                  AND (d.expires_at IS NULL OR d.expires_at>?) AND ({' OR '.join(clauses)})
                  {embedding_filter} {paper_filter} ORDER BY c.paper_id,c.ordinal"""
        # expires_at is deliberately checked in SQL on every query.
        params.insert(1, now())
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]
