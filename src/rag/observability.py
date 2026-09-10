from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .errors import redact

STAGES = {"discover", "parse", "ocr", "chunk", "embedding", "index", "retrieve", "generate"}


class JsonLogger:
    """Short append-only records; no document text or credentials are accepted."""

    def __init__(self, path: Path, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: object) -> None:
        if not self.enabled:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{k: redact(v) if isinstance(v, str) else v for k, v in fields.items()},
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    @contextmanager
    def stage(self, stage: str, **context: object) -> Iterator[dict]:
        if stage not in STAGES:
            raise ValueError(f"unknown stage: {stage}")
        started = time.perf_counter()
        result: dict = {}
        self.emit("stage_started", stage=stage, **context)
        try:
            yield result
        except Exception as exc:
            self.emit(
                "stage_failed",
                stage=stage,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                exception_type=type(exc).__name__,
                message=str(exc),
                **context,
            )
            raise
        else:
            self.emit(
                "stage_completed",
                stage=stage,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                **context,
                **result,
            )
