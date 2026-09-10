from __future__ import annotations

import re
import traceback
from dataclasses import asdict, dataclass

SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)((?:api[_-]?key|token|password)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
]


def redact(value: str, limit: int = 2000) -> str:
    safe = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            safe = pattern.sub(r"\1[REDACTED]", safe)
        else:
            safe = pattern.sub("[REDACTED]", safe)
    return safe[:limit]


@dataclass
class ErrorInfo:
    error_code: str
    stage: str
    message: str
    exception_type: str
    retryable: bool
    traceback: str

    def to_dict(self) -> dict:
        return asdict(self)


class RagError(RuntimeError):
    def __init__(self, code: str, stage: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable


def error_info(exc: BaseException, stage: str, debug: bool = False) -> ErrorInfo:
    if isinstance(exc, RagError):
        code, actual_stage, retryable = exc.code, exc.stage, exc.retryable
    else:
        code, actual_stage, retryable = f"{stage.upper()}_FAILED", stage, False
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return ErrorInfo(
        error_code=code,
        stage=actual_stage,
        message=redact(str(exc), 1000),
        exception_type=type(exc).__name__,
        retryable=retryable,
        traceback=redact(trace, 8000 if debug else 2000),
    )

