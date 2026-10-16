"""End to end (§11): upload → preview → confirm → report → delete, through the real HTTP API.

Also the database check: after a full run, no database row or log line may contain a seeded PII value.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from conftest import requires_ocr, wait_for
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session
from synthetic import make_return

from app.main import create_app
from app.models import Job, JobStatus
from app.settings import Settings

pytestmark = [requires_ocr, pytest.mark.slow]


def _upload(client: TestClient, pdf: bytes, **questionnaire: str) -> str:
    response = client.post(
        "/api/jobs",
        files={"file": ("return.pdf", pdf, "application/pdf")},
        data={"metadata": json.dumps({"tax_year": 2025, "questionnaire": questionnaire})},
    )
    assert response.status_code == 201, response.text
    return response.json()["job_id"]


def _status(client: TestClient, job_id: str) -> str:
    return client.get(f"/api/jobs/{job_id}").json()["status"]


def _all_rows(settings: Settings) -> str:
    from sqlalchemy import create_engine

    engine = create_engine(settings.database_url)
    dump: list[str] = []
    with engine.connect() as connection:
        for table in ("jobs", "reports"):
            for row in connection.execute(text(f"SELECT * FROM {table}")):
                dump.append(json.dumps([str(v) for v in row]))
    engine.dispose()
    return "\n".join(dump)


def _stored_files(settings: Settings, job_id: str) -> list[str]:
    """Works for local storage and for S3/MinIO in CI."""
    from datetime import UTC, datetime, timedelta

    from app.storage import build_storage

    backend = build_storage(settings)._backend
    return list(backend.list_older_than(f"jobs/{job_id}/", datetime.now(UTC) + timedelta(days=1)))


def test_full_flow(migrated_settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sample = make_return("text", seed=11)
    app = create_app(migrated_settings)
    with TestClient(app) as client:
        job_id = _upload(client, sample.pdf, paid_childcare="yes")
        wait_for(lambda: _status(client, job_id) in {"awaiting_confirmation", "failed"})
        assert _status(client, job_id) == "awaiting_confirmation"

        redactions = client.get(f"/api/jobs/{job_id}/redactions").json()
        regions: list[dict[str, Any]] = redactions["regions"]
        assert regions and len(redactions["pages"]) == 4
        assert all(set(r) == {"id", "page", "x0", "y0", "x1", "y1", "kind", "detector", "score"} for r in regions)

        # Preview images: original and redacted pages, and the user can add and remove regions.
        assert client.get(f"/api/jobs/{job_id}/pages/0?redacted=false").headers["content-type"] == "image/png"
        assert client.get(f"/api/jobs/{job_id}/pages/0?redacted=true").status_code == 200
        added = client.patch(
            f"/api/jobs/{job_id}/redactions",
            json={"add": [{"page": 0, "x0": 300, "y0": 700, "x1": 350, "y1": 720}], "remove_ids": [regions[0]["id"]]},
        ).json()["regions"]
        assert len(added) == len(regions)
        assert any(r["kind"] == "user" for r in added)
        assert regions[0]["id"] not in {r["id"] for r in added}
        # Put the removed region back so nothing seeded leaks into the review input.
        client.patch(f"/api/jobs/{job_id}/redactions", json={"add": [{k: regions[0][k] for k in ("page", "x0", "y0", "x1", "y1")}]})

        # Nothing goes to the LLM before confirmation.
        assert client.get(f"/api/jobs/{job_id}/report").status_code == 404
        assert client.post(f"/api/jobs/{job_id}/confirm").status_code == 202
        assert client.post(f"/api/jobs/{job_id}/confirm").status_code == 409
        wait_for(lambda: _status(client, job_id) in {"done", "failed"})
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "done", job

        # The original is deleted once redaction is confirmed.
        assert client.get(f"/api/jobs/{job_id}/pages/0?redacted=false").status_code == 404
        stored = {p.rsplit("/", 1)[-1] for p in _stored_files(migrated_settings, job_id)}
        assert stored == {"redacted.pdf", "pii_map.json", "review_input.json"}

        report = client.get(f"/api/jobs/{job_id}/report").json()
        assert report["tax_year"] == 2025 and report["findings"]
        html = client.get(f"/api/jobs/{job_id}/report.html")
        assert "educational review aid" in html.text and "default-src 'none'" in html.headers["content-security-policy"]
        markdown = client.get(f"/api/jobs/{job_id}/report.md").text
        assert "## What we couldn't read" in markdown and "## Next steps" in markdown

        # Database check (§11): no seeded value in any row or log line.
        rows = _all_rows(migrated_settings).lower()
        logs = "\n".join(r.getMessage() for r in caplog.records).lower()
        for value in sample.pii:
            if len(value) > 3:
                assert value.lower() not in rows, "seeded PII found in the database"
                assert value.lower() not in logs, "seeded PII found in the logs"

        assert client.delete(f"/api/jobs/{job_id}").status_code == 204
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        assert _stored_files(migrated_settings, job_id) == []
        assert job_id not in _all_rows(migrated_settings)


def test_upload_validation(migrated_settings: Settings) -> None:
    import pymupdf

    with TestClient(create_app(migrated_settings)) as client:
        def post(content: bytes, year: int = 2025) -> Any:
            return client.post(
                "/api/jobs",
                files={"file": ("x.pdf", content, "application/pdf")},
                data={"metadata": json.dumps({"tax_year": year})},
            )

        assert post(b"hello").json()["error"] == "not_a_pdf"
        assert post(b"%PDF-1.7 garbage").json()["error"] == "pdf_unreadable"
        assert post(make_return("text", 1).pdf, year=2019).json()["error"] == "unsupported_tax_year"

        locked = pymupdf.open()
        locked.new_page()
        encrypted = locked.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
        assert post(encrypted).json()["error"] == "pdf_password_protected"

        too_long = pymupdf.open()
        for _ in range(migrated_settings.max_pages + 1):
            too_long.new_page()
        assert post(too_long.tobytes()).json()["error"] == "too_many_pages"

        huge = b"%PDF-" + b"0" * (migrated_settings.max_upload_bytes + 10)
        assert post(huge).status_code == 413


def test_jobs_are_filtered_by_user(migrated_settings: Settings) -> None:
    from datetime import UTC, datetime, timedelta

    from app.db import build_engine

    engine = build_engine(migrated_settings.database_url)
    with Session(engine) as session:
        other = Job(user_id="someone-else", tax_year=2025, status=JobStatus.DONE,
                    expires_at=datetime.now(UTC) + timedelta(hours=1))
        session.add(other)
        session.commit()
        other_id = other.id
    engine.dispose()
    with TestClient(create_app(migrated_settings)) as client:
        assert client.get(f"/api/jobs/{other_id}").status_code == 404
        assert client.delete(f"/api/jobs/{other_id}").status_code == 404
        assert other_id not in [j["id"] for j in client.get("/api/jobs").json()]
