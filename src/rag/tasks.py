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
            job = self.pipeline.store.claim_ingestion_job()
            if not job:
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue
            self.pipeline.ingest(
                file=Path(job["file_path"]) if job["file_path"] else None,
                force=bool(job["force"]), run_id=job["run_id"], request_id=job["request_id"],
            )
            time.sleep(0)
