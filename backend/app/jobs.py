from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import select

from .db import session_scope
from .fields import build_review_input
from .models import Job, JobStatus, Report
from .pdf import RedactionRegion, build_pdf_backend
from .pii import detect_redactions
from .report import render_html, render_markdown
from .review import build_llm_client, load_rules
from .settings import AuthMode, settings
from .storage import EncryptedStorage, build_storage


class JobService:
    def __init__(self) -> None:
        self.pdf = build_pdf_backend()
        self.storage: EncryptedStorage = build_storage()
        self.llm = build_llm_client()
        self.executor = ThreadPoolExecutor(max_workers=max(settings.workers, 1), thread_name_prefix="job-worker")

    def store_original(self, job_id: str, content: bytes) -> None:
        self.storage.put(f"jobs/{job_id}/original.pdf", content)

    def get_original(self, job_id: str) -> bytes:
        return self.storage.get(f"jobs/{job_id}/original.pdf")

    def get_redacted(self, job_id: str) -> bytes:
        return self.storage.get(f"jobs/{job_id}/redacted.pdf")

    def submit_redaction(self, job_id: str) -> None:
        self.executor.submit(self._run_redaction, job_id)

    def submit_review(self, job_id: str) -> None:
        self.executor.submit(self._run_review, job_id)

    def _run_redaction(self, job_id: str) -> None:
        try:
            original = self.get_original(job_id)
            words = self.pdf.extract_words(original)
            page_count = self.pdf.page_count(original)
            detections = detect_redactions(words)
            redacted = self.pdf.redact(original, detections)
            redacted = self.pdf.sanitize(redacted)
            self.storage.put(f"jobs/{job_id}/redacted.pdf", redacted)
            with session_scope() as session:
                job = session.get(Job, job_id)
                if not job:
                    return
                job.page_count = page_count
                job.status = JobStatus.AWAITING_CONFIRMATION
                job.redaction_log = [region.__dict__ for region in detections]
                job.updated_at = datetime.now(UTC)
                session.add(job)
                session.commit()
        except Exception:
            self._mark_failed(job_id, "redaction_failed")

    def _run_review(self, job_id: str) -> None:
        try:
            with session_scope() as session:
                job = session.get(Job, job_id)
                if not job:
                    return
                payload = build_review_input(job)

            rules, rules_version = load_rules(payload.tax_year)
            result, input_tokens, output_tokens = self.llm.analyze(payload, rules)
            self._validate_finding_fields(result.model_dump(mode="json"), payload.model_dump(mode="json"))

            report_json = result.model_dump(mode="json")
            with session_scope() as session:
                job = session.get(Job, job_id)
                if not job:
                    return
                job.status = JobStatus.DONE
                job.updated_at = datetime.now(UTC)
                session.add(job)
                session.merge(
                    Report(
                        job_id=job_id,
                        model=settings.llm_model,
                        rules_version=rules_version,
                        findings=report_json,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                )
                session.commit()
        except Exception:
            self._mark_failed(job_id, "review_failed")

    def _mark_failed(self, job_id: str, error_code: str) -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if not job:
                return
            job.status = JobStatus.FAILED
            job.error_code = error_code
            job.updated_at = datetime.now(UTC)
            session.add(job)
            session.commit()

    def _validate_finding_fields(self, output: dict[str, Any], payload: dict[str, Any]) -> None:
        flat = set(self._flatten_paths(payload))
        for finding in output.get("findings", []):
            for field in finding.get("evidence_fields", []):
                if field not in flat:
                    raise ValueError(f"LLM output referenced unknown field: {field}")

    def _flatten_paths(self, value: Any, prefix: str = "") -> list[str]:
        if isinstance(value, dict):
            paths: list[str] = []
            for key, nested in value.items():
                child = f"{prefix}.{key}" if prefix else key
                paths.extend(self._flatten_paths(nested, child))
            return paths
        return [prefix]

    def render_report_html(self, report_payload: dict[str, Any], tax_year: int, filing_status: str) -> str:
        from .review import ReviewResult

        parsed = ReviewResult.model_validate(report_payload)
        return render_html(parsed, tax_year=tax_year, filing_status=filing_status)

    def render_report_markdown(self, report_payload: dict[str, Any], tax_year: int, filing_status: str) -> str:
        from .review import ReviewResult

        parsed = ReviewResult.model_validate(report_payload)
        return render_markdown(parsed, tax_year=tax_year, filing_status=filing_status)

    def cleanup_expired(self) -> None:
        now = datetime.now(UTC)
        with session_scope() as session:
            expired_jobs = session.exec(select(Job).where(Job.expires_at < now)).all()
            for job in expired_jobs:
                self.storage.delete_prefix(f"jobs/{job.id}")
                report = session.get(Report, job.id)
                if report:
                    session.delete(report)
                session.delete(job)
            session.commit()

    def startup_recover(self) -> None:
        with session_scope() as session:
            interrupted = session.exec(
                select(Job).where(Job.status.in_([JobStatus.REDACTING, JobStatus.REVIEWING]))
            ).all()
            for job in interrupted:
                job.status = JobStatus.FAILED
                job.error_code = "interrupted"
                job.updated_at = datetime.now(UTC)
                session.add(job)
            session.commit()

    def startup_warning(self) -> str | None:
        if settings.auth_mode != AuthMode.NONE and settings.pdf_backend.value == "pymupdf":
            if settings.license_path_confirmed not in {"A", "B"}:
                return (
                    "PyMuPDF is enabled for network-accessible mode and LICENSE_PATH_CONFIRMED "
                    "is not set to A or B."
                )
        return None

    @staticmethod
    def next_expiry() -> datetime:
        return datetime.now(UTC) + timedelta(hours=settings.retention_hours)
