from __future__ import annotations

import threading
import time
from pathlib import Path

from .pipeline import Pipeline


class IngestionWorker:
    """Single durable SQLite-backed worker suitable for this small corpus."""

    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last_cleanup = 0.0
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        interrupted = self.pipeline.store.recover_interrupted_runs()
        if interrupted:
            self.pipeline.log.emit("tasks_interrupted_on_startup", count=interrupted)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rag-ingestion-worker")
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if time.monotonic() - self._last_cleanup >= 60:
                try:
                    self.pipeline.cleanup_expired_documents()
                except Exception as exc:
                    # Per-document failures are normally captured by the pipeline;
                    # an unexpected cleanup failure must not stop ingestion work.
                    self.pipeline.log.emit("cleanup_failed", stage="cleanup",
                                           error_code="TEMP_CLEANUP_FAILED",
                                           exception_type=type(exc).__name__)
                self._last_cleanup = time.monotonic()
            job = self.pipeline.store.claim_ingestion_job()
            if not job:
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue
            self.pipeline.ingest(
                file=Path(job["file_path"]) if job["file_path"] else None,
                force=bool(job["force"]), run_id=job["run_id"], request_id=job["request_id"],
                document_scope={
                    "document_id": job["document_id"], "collection_id": job["collection_id"],
                    "tenant_id": job["tenant_id"], "owner_id": job["owner_id"],
                    "scope_type": job["scope_type"], "conversation_id": job["conversation_id"],
                    "expires_at": job["expires_at"],
                } if job.get("collection_id") else None,
            )
            time.sleep(0)
