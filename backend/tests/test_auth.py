from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE, SessionSigner, session_key
from app.main import create_app


def _client(make_settings: Any, **overrides: Any) -> TestClient:
    from app.db import run_migrations

    settings = make_settings(
        AUTH_MODE="access_code", ACCESS_CODE="pilot-code", LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="unused", **overrides
    )
    run_migrations(settings.database_url)
    return TestClient(create_app(settings, start_background_tasks=False))


def test_access_code_flow(make_settings: Any) -> None:
    with _client(make_settings) as client:
        assert client.get("/api/jobs").status_code == 401
        assert client.post("/api/access", json={"code": "wrong"}).status_code == 401
        response = client.post("/api/access", json={"code": "pilot-code"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert client.get("/api/jobs").status_code == 200
        # API docs are disabled outside development (§9).
        # (The SPA shell may answer these paths once the frontend is built, but never with the API schema.)
        assert '"openapi"' not in client.get("/openapi.json").text
        assert "swagger" not in client.get("/docs").text.lower()


def test_access_code_is_rate_limited(make_settings: Any) -> None:
    with _client(make_settings) as client:
        codes = [client.post("/api/access", json={"code": "nope"}).status_code for _ in range(12)]
        assert codes[:10] == [401] * 10
        assert codes[-1] == 429


def test_tampered_or_expired_cookies_are_rejected(make_settings: Any) -> None:
    settings = make_settings(AUTH_MODE="access_code", ACCESS_CODE="pilot-code", LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="unused")
    signer = SessionSigner(session_key(settings))
    token = signer.issue("local")
    assert signer.verify(token) == "local"
    assert signer.verify(token.replace("local", "admin")) is None
    assert signer.verify(signer.issue("local", ttl_seconds=-1)) is None
    with _client(make_settings) as client:
        client.cookies.set(SESSION_COOKIE, token.replace("local", "other"))
        assert client.get("/api/jobs").status_code == 401


def test_session_key_is_not_the_file_key(make_settings: Any) -> None:
    settings = make_settings()
    assert session_key(settings) != settings.file_encryption_key.encode()


def test_security_headers(make_settings: Any) -> None:
    from app.db import run_migrations

    settings = make_settings()
    run_migrations(settings.database_url)
    with TestClient(create_app(settings, start_background_tasks=False)) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
