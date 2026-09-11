from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Principal:
    """Verified caller identity supplied by one centralized FastAPI dependency."""

    tenant_id: str
    subject: str
    roles: frozenset[str]
    auth_method: str

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


ANONYMOUS = Principal("default", "anonymous", frozenset(), "anonymous")


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


def principal_from_request(request: Request) -> Principal:
    """Resolve a trusted identity; arbitrary X-User-ID is intentionally ignored.

    The existing administrator cookie remains supported. Local user simulation is
    opt-in and is disabled when RAG_ENV=production, making the insecure boundary
    explicit instead of silently trusting a caller-controlled production header.
    """
    current_security = getattr(request.app.state, "security", None)
    if current_security is None:
        current_security = SessionSecurity()
    if current_security.valid(request.cookies.get(current_security.cookie_name)):
        return Principal(
            os.getenv("RAG_DEFAULT_TENANT_ID", "default"),
            "admin",
            frozenset({"admin", "user"}),
            "admin_session",
        )
    adapter = getattr(request.app.state, "principal_resolver", None)
    if callable(adapter):
        resolved = adapter(request)
        if isinstance(resolved, Principal):
            return resolved
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_ADAPTER_INVALID"})
    proxy_secret = os.getenv("RAG_TRUSTED_IDENTITY_SECRET", "")
    if proxy_secret:
        tenant = request.headers.get("x-rag-tenant", "").strip()
        subject = request.headers.get("x-rag-subject", "").strip()
        roles_text = request.headers.get("x-rag-roles", "user").strip()
        timestamp = request.headers.get("x-rag-identity-timestamp", "").strip()
        signature = request.headers.get("x-rag-identity-signature", "").strip()
        try:
            fresh = abs(int(time.time()) - int(timestamp)) <= 60
        except ValueError:
            fresh = False
        canonical = "\n".join((tenant, subject, roles_text, timestamp)).encode()
        expected = hmac.new(proxy_secret.encode(), canonical, hashlib.sha256).hexdigest()
        if tenant and subject and fresh and hmac.compare_digest(signature, expected):
            roles = frozenset(role.strip() for role in roles_text.split(",") if role.strip())
            return Principal(tenant, subject, roles, "trusted_proxy_hmac")
        if any((tenant, subject, timestamp, signature)):
            raise HTTPException(status_code=401, detail={"code": "TRUSTED_IDENTITY_INVALID"})
    dev_enabled = os.getenv("RAG_DEV_AUTH_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    production = os.getenv("RAG_ENV", "development").lower() == "production"
    dev_subject = request.headers.get("x-rag-dev-subject", "").strip()
    if dev_enabled and not production and dev_subject:
        if len(dev_subject) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-@" for ch in dev_subject):
            raise HTTPException(status_code=401, detail={"code": "DEV_IDENTITY_INVALID"})
        return Principal(
            os.getenv("RAG_DEFAULT_TENANT_ID", "default"),
            dev_subject,
            frozenset({"user"}),
            "explicit_dev_header",
        )
    return ANONYMOUS


def require_user(principal: Principal) -> Principal:
    if principal.subject == "anonymous":
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "需要已验证的用户身份"})
    return principal


def require_admin_principal(principal: Principal) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "需要管理员权限"})
    return principal
