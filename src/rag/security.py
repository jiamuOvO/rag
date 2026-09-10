from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time


def hash_password(password: str, *, iterations: int = 310_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                     base64.urlsafe_b64decode(raw_salt), int(raw_iterations))
        return hmac.compare_digest(actual, base64.urlsafe_b64decode(raw_digest))
    except (ValueError, TypeError):
        return False


class SessionSecurity:
    cookie_name = "rag_admin_session"

    def __init__(self) -> None:
        self.password_hash = os.getenv("RAG_ADMIN_PASSWORD_HASH", "")
        supplied_secret = os.getenv("RAG_SESSION_SECRET", "")
        self.secret = (supplied_secret or hashlib.sha256(self.password_hash.encode()).hexdigest()).encode()
        self.ttl_seconds = int(os.getenv("RAG_SESSION_TTL_SECONDS", "28800"))
        self.cookie_secure = os.getenv("RAG_COOKIE_SECURE", "0").lower() in {"1", "true", "yes", "on"}

    @property
    def configured(self) -> bool:
        return bool(self.password_hash)

    def login(self, password: str) -> str | None:
        if not self.configured or not verify_password(password, self.password_hash):
            return None
        expires = str(int(time.time()) + self.ttl_seconds)
        signature = hmac.new(self.secret, expires.encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def valid(self, token: str | None) -> bool:
        if not self.configured or not token:
            return False
        try:
            expires, signature = token.split(".", 1)
            expected = hmac.new(self.secret, expires.encode(), hashlib.sha256).hexdigest()
            return int(expires) >= int(time.time()) and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False
