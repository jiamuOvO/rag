from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _yaml_config(project: Path) -> dict:
    path = Path(os.getenv("RAG_CONFIG_FILE", str(project / "config.yaml")))
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        value = yaml.safe_load(fh) or {}
    if not isinstance(value, dict):
        raise ValueError("config.yaml root must be a mapping")
    for section in ("chat", "embedding"):
        values = value.get(section, {})
        if isinstance(values, dict) and "api_key" in values:
            raise ValueError(
                f"Do not store api_key in config.yaml; use {section}.api_key_env and an environment variable"
            )
    return value


def _nested(config: dict, section: str, key: str, default=None):
    value = config.get(section, {})
    return value.get(key, default) if isinstance(value, dict) else default


def _env_bool(name: str, yaml_value: object, default: bool) -> bool:
    if os.getenv(name) is not None:
        return _bool(name, default)
    return bool(default if yaml_value is None else yaml_value)


@dataclass(frozen=True)
class Settings:
    project_dir: Path
    data_dir: Path
    var_dir: Path
    db_path: Path
    log_path: Path
    debug: bool
    ocr_enabled: bool
    min_page_chars: int
    chunk_tokens: int
    chunk_overlap: int
    chat_provider: str
    chat_base_url: str | None
    chat_api_key: str | None
    chat_model: str | None
    chat_timeout_seconds: float
    embedding_provider: str = "disabled"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_timeout_seconds: float = 60.0
    embedding_batch_size: int = 10
    reranker_provider: str = "disabled"
    structured_extraction_enabled: bool = False

    @classmethod
    def load(cls) -> "Settings":
        project = Path(__file__).resolve().parents[2]
        config = _yaml_config(project)
        data = Path(os.getenv("RAG_DATA_DIR", _nested(config, "paths", "data_dir", str(project / "data")))).resolve()
        var = Path(os.getenv("RAG_VAR_DIR", _nested(config, "paths", "var_dir", str(project / "var")))).resolve()
        key_env = str(_nested(config, "chat", "api_key_env", "RAG_CHAT_API_KEY"))
        embedding_key_env = str(_nested(config, "embedding", "api_key_env", key_env))
        embedding_base_url = _nested(config, "embedding", "base_url")
        return cls(
            project_dir=project,
            data_dir=data,
            var_dir=var,
            db_path=var / "rag.sqlite3",
            log_path=var / "logs" / "rag.jsonl",
            debug=_env_bool("RAG_DEBUG", _nested(config, "observability", "debug"), False),
            ocr_enabled=_env_bool("RAG_OCR_ENABLED", _nested(config, "ocr", "enabled"), True),
            min_page_chars=int(os.getenv("RAG_MIN_PAGE_CHARS", _nested(config, "ocr", "min_page_chars", 80))),
            chunk_tokens=int(os.getenv("RAG_CHUNK_TOKENS", _nested(config, "chunking", "target_tokens", 384))),
            chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", _nested(config, "chunking", "overlap_tokens", 64))),
            chat_provider=os.getenv("RAG_CHAT_PROVIDER", _nested(config, "chat", "provider", "extractive")).strip().lower(),
            chat_base_url=os.getenv("RAG_CHAT_BASE_URL", _nested(config, "chat", "base_url")),
            chat_api_key=os.getenv(key_env),
            chat_model=os.getenv("RAG_CHAT_MODEL", _nested(config, "chat", "model")),
            chat_timeout_seconds=float(os.getenv("RAG_CHAT_TIMEOUT_SECONDS", _nested(config, "chat", "timeout_seconds", 60))),
            embedding_provider=os.getenv("RAG_EMBEDDING_PROVIDER", _nested(config, "embedding", "provider", "disabled")).strip().lower(),
            embedding_base_url=os.getenv(
                "RAG_EMBEDDING_BASE_URL",
                embedding_base_url,
            ),
            embedding_api_key=os.getenv(embedding_key_env),
            embedding_model=os.getenv("RAG_EMBEDDING_MODEL", _nested(config, "embedding", "model")),
            embedding_timeout_seconds=float(os.getenv(
                "RAG_EMBEDDING_TIMEOUT_SECONDS", _nested(config, "embedding", "timeout_seconds", 60)
            )),
            embedding_batch_size=int(os.getenv(
                "RAG_EMBEDDING_BATCH_SIZE", _nested(config, "embedding", "batch_size", 10)
            )),
            reranker_provider=os.getenv(
                "RAG_RERANKER_PROVIDER", _nested(config, "reranker", "provider", "disabled")
            ).strip().lower(),
            structured_extraction_enabled=_env_bool(
                "RAG_STRUCTURED_EXTRACTION_ENABLED",
                _nested(config, "structured_extraction", "enabled"), False,
            ),
        )

    def ensure_runtime_dirs(self) -> None:
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
