from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session

from app.db import build_engine
from app.jobs import JobService
from app.models import Job, JobStatus, Report
from app.pdf import build_pdf_backend
from app.review import FakeLlmClient
from app.settings import Settings
from app.storage import EncryptedStorage, LocalStorage, build_storage


def test_files_are_encrypted_at_rest(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    local = LocalStorage(tmp_path)
    storage = EncryptedStorage(local, key)
    storage.put("jobs/a/original.pdf", b"%PDF- secret content")
    assert storage.get("jobs/a/original.pdf") == b"%PDF- secret content"
    assert b"secret" not in (tmp_path / "jobs/a/original.pdf").read_bytes()
    storage.delete_prefix("jobs/a/")
    assert not (tmp_path / "jobs/a").exists()
    with pytest.raises(FileNotFoundError):
        storage.get("jobs/a/original.pdf")
    with pytest.raises(ValueError):
        local.put("../escape", b"x")


def _service(settings: Settings) -> JobService:
    return JobService(
        settings=settings,
        engine=build_engine(settings.database_url),
        storage=build_storage(settings),
        pdf=build_pdf_backend(settings.pdf_backend, 30, isolated=False),
        llm=FakeLlmClient(),
    )


def _job(service: JobService, **fields: Any) -> str:
    with Session(service.engine) as session:
        job = Job(tax_year=2025, expires_at=datetime.now(UTC) + timedelta(hours=1), **fields)
        session.add(job)
        session.commit()
        return job.id


def test_cleanup_deletes_expired_files_then_rows(migrated_settings: Settings) -> None:
    service = _service(migrated_settings)
    expired = _job(service, status=JobStatus.DONE)
    fresh = _job(service, status=JobStatus.DONE)
    with Session(service.engine) as session:
        job = session.get(Job, expired)
        assert job is not None
        job.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.add(job)
        session.add(Report(job_id=expired, model="fake", rules_version="x", findings={}))
        session.commit()
    for job_id in (expired, fresh):
        service.storage.put(f"jobs/{job_id}/redacted.pdf", b"data")

    assert service.cleanup_expired() == 1
    with Session(service.engine) as session:
        assert session.get(Job, expired) is None and session.get(Report, expired) is None
        assert session.get(Job, fresh) is not None
    with pytest.raises(FileNotFoundError):
        service.storage.get(f"jobs/{expired}/redacted.pdf")
    assert service.storage.get(f"jobs/{fresh}/redacted.pdf") == b"data"
    service.shutdown()


def test_restart_marks_running_jobs_interrupted(migrated_settings: Settings) -> None:
    service = _service(migrated_settings)
    running = [_job(service, status=status) for status in (JobStatus.REDACTING, JobStatus.REVIEWING)]
    waiting = _job(service, status=JobStatus.AWAITING_CONFIRMATION)
    assert service.startup_recover() == 2
    with Session(service.engine) as session:
        for job_id in running:
            job = session.get(Job, job_id)
            assert job is not None and job.status == JobStatus.FAILED and job.error_code == "interrupted"
        job = session.get(Job, waiting)
        assert job is not None and job.status == JobStatus.AWAITING_CONFIRMATION
    service.shutdown()


def test_license_reminder(make_settings: Any) -> None:
    def warning(**overrides: Any) -> str | None:
        settings = make_settings(
            AUTH_MODE="access_code", ACCESS_CODE="x", LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="unused", **overrides
        )
        return _service(settings).license_warning()

    assert warning(PDF_BACKEND="pymupdf")
    assert warning(PDF_BACKEND="pymupdf", LICENSE_PATH_CONFIRMED="B") is None
    assert warning(PDF_BACKEND="permissive") is None
