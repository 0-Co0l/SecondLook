from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import run_migrations  # noqa: E402
from app.settings import Settings  # noqa: E402

TEST_KEY = Fernet.generate_key().decode()


def tesseract_ready() -> bool:
    if not shutil.which("tesseract"):
        return False
    result = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, check=False)
    return "eng" in result.stdout.split()


requires_ocr = pytest.mark.skipif(not tesseract_ready(), reason="Tesseract with English data is not installed")


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., Settings]:
    """Settings that ignore the developer's .env. CI points DATABASE_URL / S3_* at Postgres and MinIO."""

    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "APP_HOST": "127.0.0.1",
            "DATA_DIR": str(tmp_path / "data"),
            "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}"),
            "STORAGE_BACKEND": os.environ.get("TEST_STORAGE_BACKEND", "local"),
            "S3_ENDPOINT_URL": os.environ.get("TEST_S3_ENDPOINT_URL"),
            "S3_BUCKET": os.environ.get("TEST_S3_BUCKET"),
            "S3_ACCESS_KEY": os.environ.get("TEST_S3_ACCESS_KEY"),
            "S3_SECRET_KEY": os.environ.get("TEST_S3_SECRET_KEY"),
            "FILE_ENCRYPTION_KEY": TEST_KEY,
            "LLM_PROVIDER": "fake",
            "AUTH_MODE": "none",
            "RETENTION_HOURS": 24,
            "PDF_BACKEND": os.environ.get("TEST_PDF_BACKEND", "pymupdf"),
            "WORKERS": 1,
            "LOG_LEVEL": "info",
        }
        values.update(overrides)
        return Settings(_env_file=None, **{k: v for k, v in values.items() if v is not None})

    return factory


@pytest.fixture
def migrated_settings(make_settings: Callable[..., Settings]) -> Iterator[Settings]:
    settings = make_settings()
    run_migrations(settings.database_url)
    yield settings
    if not settings.database_url.startswith("sqlite"):
        _reset_database(settings.database_url)


def _reset_database(url: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS reports, jobs, alembic_version CASCADE"))
    engine.dispose()


def wait_for(predicate: Callable[[], bool], timeout: float = 120.0, interval: float = 0.25) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met before timeout")
