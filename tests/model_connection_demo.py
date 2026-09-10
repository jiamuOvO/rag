"""Minimal Chat and Embedding connection diagnostic for the real app providers."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rag.config import Settings  # noqa: E402
from rag.models import Evidence  # noqa: E402
from rag.providers import OpenAICompatibleEmbeddingProvider, OpenAICompatibleProvider  # noqa: E402


def check(name: str, operation) -> dict:
    started = time.perf_counter()
    try:
        detail = operation()
        return {"ok": True, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), **detail}
    except Exception as exc:
        cause = exc.__cause__
        return {
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_code": getattr(exc, "code", f"{name.upper()}_CHECK_FAILED"),
            "exception_type": type(exc).__name__,
            "cause_type": type(cause).__name__ if cause else None,
            "http_status": getattr(cause, "code", None),
            "message": str(exc),
        }


def check_embedding(settings: Settings) -> dict:
    vectors = OpenAICompatibleEmbeddingProvider(settings).embed(["endpoint connection check"])
    return {"model": settings.embedding_model, "vector_count": len(vectors), "dimensions": len(vectors[0])}


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Chat 和 Embedding 两个 OpenAI-compatible 接口。")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config_test.yaml"))
    args = parser.parse_args()
    config_path = args.config.expanduser().resolve()
    os.environ["RAG_CONFIG_FILE"] = str(config_path)

    try:
        if not config_path.is_file():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        settings = Settings.load()
        missing_keys = []
        if not settings.chat_api_key:
            missing_keys.append("Chat API Key")
        if not settings.embedding_api_key:
            missing_keys.append("Embedding API Key")
        if missing_keys:
            raise ValueError("当前进程未设置: " + ", ".join(missing_keys))

        evidence = [Evidence(
            evidence_id="endpoint-check-1", chunk_id="endpoint-check-1",
            paper_id="endpoint-check", paper_name="Endpoint check",
            page_start=1, page_end=1, section_path="test",
            excerpt="The endpoint check value is 42.", score=1.0,
        )]
        chat = check("chat", lambda: {
            "model": settings.chat_model,
            "answer_preview": OpenAICompatibleProvider(settings)
            .answer("What is the endpoint check value?", evidence)[:160],
        })
        embedding = check("embedding", lambda: check_embedding(settings))
        result = {
            "success": chat["ok"] and embedding["ok"],
            "config": str(config_path),
            "chat_api_key_present": True,
            "embedding_api_key_present": True,
            "chat": chat,
            "embedding": embedding,
        }
    except Exception as exc:
        result = {
            "success": False,
            "config": str(config_path),
            "error_code": "MODEL_CHECK_SETUP_FAILED",
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
