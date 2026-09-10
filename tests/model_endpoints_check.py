"""Check both model endpoints through the same providers used by the application."""

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


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Chat 和 Embedding 两个 OpenAI-compatible 接口。")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "config.yaml")
    args = parser.parse_args()
    config_path = args.config.expanduser().resolve()
    os.environ["RAG_CONFIG_FILE"] = str(config_path)

    try:
        settings = Settings.load()
        if not settings.chat_api_key:
            raise ValueError("当前进程未设置配置中 api_key_env 指定的 API Key 环境变量")

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
        embedding = check("embedding", lambda: _check_embedding(settings))
        result = {
            "success": chat["ok"] and embedding["ok"],
            "config": str(config_path),
            "api_key_present": True,
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

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


def _check_embedding(settings: Settings) -> dict:
    vectors = OpenAICompatibleEmbeddingProvider(settings).embed(["endpoint connection check"])
    return {"model": settings.embedding_model, "vector_count": len(vectors), "dimensions": len(vectors[0])}


if __name__ == "__main__":
    raise SystemExit(main())
