"""Minimal diagnostic for the project's OpenAI-compatible chat provider."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rag.config import Settings  # noqa: E402
from rag.errors import RagError  # noqa: E402
from rag.models import Evidence  # noqa: E402
from rag.providers import OpenAICompatibleProvider  # noqa: E402


def safe_endpoint(base_url: str | None) -> str | None:
    """Return the endpoint without credentials, query parameters, or fragments."""
    if not base_url:
        return None
    parts = urlsplit(base_url.rstrip("/") + "/chat/completions")
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只测试大模型连接，不执行检索或入库。")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config_test.yaml"),
        help="测试配置文件路径（默认：tests/config_test.yaml）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.expanduser().resolve()
    os.environ["RAG_CONFIG_FILE"] = str(config_path)
    started = time.perf_counter()

    try:
        if not config_path.is_file():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        settings = Settings.load()
        endpoint = safe_endpoint(settings.chat_base_url)
        missing = []
        if settings.chat_provider != "openai_compatible":
            missing.append("chat.provider 必须是 openai_compatible")
        if not settings.chat_base_url or "REPLACE_WITH_" in settings.chat_base_url:
            missing.append("请在 config_test.yaml 中填写真实 chat.base_url")
        if not settings.chat_model or "REPLACE_WITH_" in settings.chat_model:
            missing.append("请在 config_test.yaml 中填写真实 chat.model")
        if not settings.chat_api_key:
            missing.append("当前终端未设置 RAG_CHAT_API_KEY")
        if missing:
            emit({
                "success": False,
                "error_code": "CHAT_CONFIG_MISSING",
                "problems": missing,
                "config": str(config_path),
                "endpoint": endpoint,
                "api_key_present": bool(settings.chat_api_key),
            })
            return 1

        provider = OpenAICompatibleProvider(settings)
        evidence = [Evidence(
            evidence_id="demo-evidence-1",
            chunk_id="demo-chunk-1",
            paper_id="demo-paper-1",
            paper_name="Connection test",
            page_start=1,
            page_end=1,
            section_path="test",
            excerpt="The connection test value is 42.",
            score=1.0,
        )]
        answer = provider.answer("What is the connection test value?", evidence)
        emit({
            "success": True,
            "provider": settings.chat_provider,
            "model": settings.chat_model,
            "endpoint": endpoint,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "answer": answer,
        })
        return 0
    except Exception as exc:
        cause = exc.__cause__
        http_status = cause.code if isinstance(cause, urllib.error.HTTPError) else None
        emit({
            "success": False,
            "error_code": exc.code if isinstance(exc, RagError) else "CHAT_TEST_FAILED",
            "exception_type": type(exc).__name__,
            "cause_type": type(cause).__name__ if cause else None,
            "http_status": http_status,
            "message": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "hints": {
                "401_or_403": "API Key 无效、权限不足，或该服务不接受 Bearer 认证。",
                "404": "base_url 通常应以 /v1 结尾；也请确认模型服务兼容 /chat/completions。",
                "timeout_or_url_error": "检查 base_url、网络、代理、防火墙以及服务是否启动。",
            },
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
