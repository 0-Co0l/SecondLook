"""Access control (§4.6). Every check goes through one AccessGuard selected by AUTH_MODE."""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from abc import ABC, abstractmethod
from collections import deque

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException, Request, Response, status

from .settings import AuthMode, Settings

SESSION_COOKIE = "secondlook_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
LOCAL_USER_ID = "local"


def session_key(settings: Settings) -> bytes:
    if settings.session_secret:
        return settings.session_secret.encode("utf-8")
    # Never reuse the file-encryption key directly; derive a separate key for cookie signatures.
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"secondlook-session-v1").derive(
        settings.file_encryption_key.encode("utf-8")
    )


class SessionSigner:
    def __init__(self, key: bytes) -> None:
        self._key = key

    def _mac(self, payload: str) -> str:
        digest = hmac.new(self._key, payload.encode("utf-8"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def issue(self, user_id: str, ttl_seconds: int = SESSION_TTL_SECONDS, now: float | None = None) -> str:
        expires = int((now or time.time()) + ttl_seconds)
        payload = f"{user_id}|{expires}"
        return f"{payload}|{self._mac(payload)}"

    def verify(self, token: str, now: float | None = None) -> str | None:
        try:
            user_id, expires, mac = token.rsplit("|", 2)
            expires_at = int(expires)
        except ValueError:
            return None
        if not hmac.compare_digest(mac, self._mac(f"{user_id}|{expires}")):
            return None
        if expires_at < (now or time.time()):
            return None
        return user_id


class RateLimiter:
    """Sliding-window per-key limiter. In-process is enough: v1 runs exactly one instance (§2)."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too_many_requests")
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory under a flood of distinct addresses
                for stale in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    del self._hits[stale]


def client_ip(request: Request) -> str:
    # Uvicorn rewrites request.client from X-Forwarded-For only for proxies in FORWARDED_ALLOW_IPS.
    return request.client.host if request.client else "unknown"


class AccessGuard(ABC):
    @abstractmethod
    def get_user_id(self, request: Request) -> str: ...

    def login(self, request: Request, response: Response, code: str) -> None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="access_code_mode_disabled")


class NoneGuard(AccessGuard):
    """Development only; settings refuse this mode unless APP_HOST is loopback."""

    def get_user_id(self, request: Request) -> str:
        return LOCAL_USER_ID


class AccessCodeGuard(AccessGuard):
    def __init__(self, settings: Settings) -> None:
        assert settings.access_code
        self._code = settings.access_code.encode("utf-8")
        self._signer = SessionSigner(session_key(settings))
        self._secure_cookie = not settings.is_local_only
        self.login_limiter = RateLimiter(limit=10, window_seconds=15 * 60)

    def get_user_id(self, request: Request) -> str:
        token = request.cookies.get(SESSION_COOKIE)
        user_id = self._signer.verify(token) if token else None
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign_in_required")
        return user_id

    def login(self, request: Request, response: Response, code: str) -> None:
        self.login_limiter.check(client_ip(request))
        if not hmac.compare_digest(code.encode("utf-8"), self._code):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_access_code")
        response.set_cookie(
            key=SESSION_COOKIE,
            value=self._signer.issue(LOCAL_USER_ID),
            httponly=True,
            secure=self._secure_cookie,
            samesite="strict",
            max_age=SESSION_TTL_SECONDS,
            path="/",
        )


class OidcGuard(AccessGuard):
    """Placeholder for a later version; sign-in is out of scope for v1 (§2)."""

    def get_user_id(self, request: Request) -> str:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="oidc_not_implemented")


def build_access_guard(settings: Settings) -> AccessGuard:
    if settings.auth_mode == AuthMode.ACCESS_CODE:
        return AccessCodeGuard(settings)
    if settings.auth_mode == AuthMode.OIDC:
        return OidcGuard()
    return NoneGuard()
