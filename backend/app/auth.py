from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod

from fastapi import HTTPException, Request, Response, status

from .settings import AuthMode, settings


SESSION_COOKIE = "secondlook_session"


def _sign(value: str) -> str:
    digest = hmac.new(
        key=settings.file_encryption_key.encode("utf-8"),
        msg=value.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"{value}.{digest}"


def _verify(value: str) -> bool:
    try:
        raw, digest = value.rsplit(".", 1)
    except ValueError:
        return False
    expected = _sign(raw).rsplit(".", 1)[1]
    return hmac.compare_digest(digest, expected)


class AccessGuard(ABC):
    @abstractmethod
    def get_user_id(self, request: Request) -> str: ...

    @abstractmethod
    def login(self, response: Response, code: str) -> None: ...


class NoneGuard(AccessGuard):
    def get_user_id(self, request: Request) -> str:
        return "local"

    def login(self, response: Response, code: str) -> None:
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="access mode disabled")


class AccessCodeGuard(AccessGuard):
    def get_user_id(self, request: Request) -> str:
        token = request.cookies.get(SESSION_COOKIE)
        if not token or not _verify(token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign in required")
        return "local"

    def login(self, response: Response, code: str) -> None:
        if code != settings.access_code:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid access code")
        response.set_cookie(
            key=SESSION_COOKIE,
            value=_sign("local"),
            httponly=True,
            secure=False if settings.app_host in {"127.0.0.1", "localhost"} else True,
            samesite="lax",
            max_age=60 * 60 * 8,
        )


class OidcGuard(AccessGuard):
    def get_user_id(self, request: Request) -> str:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="oidc mode not yet implemented")

    def login(self, response: Response, code: str) -> None:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="oidc mode not yet implemented")


def build_access_guard() -> AccessGuard:
    if settings.auth_mode == AuthMode.ACCESS_CODE:
        return AccessCodeGuard()
    if settings.auth_mode == AuthMode.OIDC:
        return OidcGuard()
    return NoneGuard()
