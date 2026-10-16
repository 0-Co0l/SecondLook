"""Job orchestration: upload → redaction preview → confirm → final redaction + review → report.

Background work runs on an in-process thread pool (§4.1); PDF parsing itself runs in short-lived child
processes (see `pdf.IsolatedPdfBackend`). Files live only in encrypted storage under `jobs/{job_id}/`.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, update
from sqlmodel import Session, select

from .fields import build_review_input, mask_words
from .models import (
    CreateJobRequest,
    Job,
    JobStatus,
    PatchRedactionsRequest,
    PiiDetectionResult,
    RedactionRegion,
    Report,
    ReportDocument,
    TaxReviewInput,
)
from .ocr import OCR_DPI, needs_ocr, ocr_page
from .pdf import PdfBackend, PdfEncryptedError, PdfError, PdfTimeoutError, PdfWord
from .pii import detect_pii
from .review import LlmClient, LlmError, RulesMissingError, finalize_review, load_rules
from .settings import AuthMode, PdfBackendName, Settings
from .storage import EncryptedStorage

logger = logging.getLogger("secondlook.jobs")

ORIGINAL = "original.pdf"
REDACTED = "redacted.pdf"
WORDS = "words.json"  # extracted text with positions; deleted at confirmation
DETECTION = "detection.json"  # word -> token map and derived attributes; no real values
PII_MAP = "pii_map.json"  # token -> real value (§5.4); encrypted like every stored file
REVIEW_INPUT = "review_input.json"


class JobRejected(Exception):
    """A request that can't be honored; `code` is safe to show and store."""

    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _key(job_id: str, name: str = "") -> str:
    return f"jobs/{job_id}/{name}"


def _log_failure(job_id: str, stage: str, exc: BaseException) -> None:
    # Exception messages can quote document content (e.g. validation errors), so only the type is logged
    # unless debug logging is on, which is for synthetic development data only.
    logger.error(
        "job %s: %s failed (%s)",
        job_id,
        stage,
        type(exc).__name__,
        exc_info=exc if logger.isEnabledFor(logging.DEBUG) else None,
    )


def extract_all_words(pdf: PdfBackend, original: bytes, page_count: int) -> tuple[list[PdfWord], list[int]]:
    """Text-layer words, with Tesseract filling in pages that have almost no text (§5.5 step 1)."""
    words = list(pdf.extract_words(original))
    unreadable: list[int] = []
    for page in range(page_count):
        if not needs_ocr(words, page):
            continue
        ocr_words = ocr_page(pdf.render_page(original, page, OCR_DPI), page, OCR_DPI)
        if ocr_words:
            words = [w for w in words if w.page != page] + ocr_words
        if not any(w.page == page for w in words):
            unreadable.append(page)
    return words, unreadable


class JobService:
    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        storage: EncryptedStorage,
        pdf: PdfBackend,
        llm: LlmClient,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.storage = storage
        self.pdf = pdf
        self.llm = llm
        self.executor = ThreadPoolExecutor(max_workers=max(settings.workers, 1), thread_name_prefix="job-worker")

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    # -- reads ---------------------------------------------------------------

    def get_owned_job(self, session: Session, job_id: str, user_id: str) -> Job | None:
        job = session.get(Job, job_id)
        if job is None or job.user_id != user_id:
            return None
        return job

    def list_jobs(self, session: Session, user_id: str) -> list[Job]:
        return list(session.exec(select(Job).where(Job.user_id == user_id).order_by(Job.created_at.desc())).all())

    def page_image(self, job: Job, page_number: int, redacted: bool) -> bytes:
        if not 0 <= page_number < job.page_count:
            raise JobRejected("page_not_found", 404)
        name = REDACTED if redacted else ORIGINAL
        try:
            source = self.storage.get(_key(job.id, name))
        except FileNotFoundError as exc:
            raise JobRejected("page_not_available", 404) from exc
        return self.pdf.render_page(source, page_number, 144)

    def page_sizes(self, job: Job) -> list[tuple[float, float]]:
        try:
            source = self.storage.get(_key(job.id, REDACTED))
        except FileNotFoundError as exc:
            raise JobRejected("page_not_available", 404) from exc
        return self.pdf.page_sizes(source)

    def report_document(self, session: Session, job: Job) -> ReportDocument | None:
        report = session.get(Report, job.id)
        return ReportDocument.model_validate(report.findings) if report else None

    # -- upload ----------------------------------------------------------------

    def create_job(self, user_id: str, raw: bytes, request: CreateJobRequest) -> Job:
        if not raw.startswith(b"%PDF-"):
            raise JobRejected("not_a_pdf")
        try:
            load_rules(request.tax_year)
        except RulesMissingError as exc:
            raise JobRejected("unsupported_tax_year") from exc
        try:
            pages = self.pdf.page_count(raw)
        except PdfEncryptedError as exc:
            raise JobRejected("pdf_password_protected") from exc
        except PdfTimeoutError as exc:
            raise JobRejected("pdf_timeout") from exc
        except PdfError as exc:
            raise JobRejected("pdf_unreadable") from exc
        if pages < 1:
            raise JobRejected("pdf_empty")
        if pages > self.settings.max_pages:
            raise JobRejected("too_many_pages")

        now = datetime.now(UTC)
        job = Job(
            user_id=user_id,
            status=JobStatus.REDACTING,
            tax_year=request.tax_year,
            page_count=pages,
            questionnaire=request.questionnaire.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=self.settings.retention_hours),
        )
        self.storage.put(_key(job.id, ORIGINAL), raw)
        try:
            with Session(self.engine) as session:
                session.add(job)
                session.commit()
                session.refresh(job)
        except Exception:
            self.storage.delete_prefix(_key(job.id))
            raise
        self.executor.submit(self._run_redaction, job.id)
        return job

    def _run_redaction(self, job_id: str) -> None:
        try:
            with Session(self.engine) as session:
                job = session.get(Job, job_id)
                if job is None:
                    return
                tax_year, page_count = job.tax_year, job.page_count
            original = self.storage.get(_key(job_id, ORIGINAL))
            words, unreadable_pages = extract_all_words(self.pdf, original, page_count)
            detection, pii_map = detect_pii(words, tax_year, self.settings.spacy_model)

            self.storage.put(_key(job_id, WORDS), json.dumps([asdict(w) for w in words]).encode())
            self.storage.put(
                _key(job_id, DETECTION),
                json.dumps(
                    {
                        "word_tokens": {str(k): v for k, v in detection.word_tokens.items()},
                        "derived": detection.derived.model_dump(mode="json"),
                        "unreadable_pages": unreadable_pages,
                    }
                ).encode(),
            )
            self.storage.put(_key(job_id, PII_MAP), json.dumps(pii_map).encode())
            self.storage.put(_key(job_id, REDACTED), self.pdf.redact(original, detection.regions))

            if not self._update(
                job_id,
                status=JobStatus.AWAITING_CONFIRMATION,
                redaction_log=[r.model_dump(mode="json") for r in detection.regions],
            ):
                self.storage.delete_prefix(_key(job_id))  # deleted by the user while we were working
        except PdfTimeoutError as exc:
            _log_failure(job_id, "redaction", exc)
            self._mark_failed(job_id, "pdf_timeout")
        except PdfError as exc:
            _log_failure(job_id, "redaction", exc)
            self._mark_failed(job_id, "pdf_unreadable")
        except Exception as exc:
            _log_failure(job_id, "redaction", exc)
            self._mark_failed(job_id, "redaction_failed")

    # -- preview edits ---------------------------------------------------------

    def update_redactions(self, session: Session, job: Job, patch: PatchRedactionsRequest) -> list[dict[str, Any]]:
        if job.status != JobStatus.AWAITING_CONFIRMATION:
            raise JobRejected("job_not_awaiting_confirmation", 409)
        remove = set(patch.remove_ids)
        regions = [RedactionRegion.model_validate(r) for r in job.redaction_log if r.get("id") not in remove]
        for box in patch.add:
            if box.page >= job.page_count:
                raise JobRejected("page_not_found", 404)
            regions.append(
                RedactionRegion(
                    **box.model_dump(), id=f"u{uuid4().hex[:10]}", kind="user", detector="user", score=1.0
                )
            )
        original = self.storage.get(_key(job.id, ORIGINAL))
        # Regenerate the preview from the original so removed regions really come back.
        self.storage.put(_key(job.id, REDACTED), self.pdf.redact(original, regions))
        job.redaction_log = [r.model_dump(mode="json") for r in regions]
        job.updated_at = datetime.now(UTC)
        session.add(job)
        session.commit()
        return job.redaction_log

    # -- confirmation and review -----------------------------------------------

    def confirm(self, job: Job) -> None:
        # Atomic status change: two confirm clicks can't start two reviews.
        with Session(self.engine) as session:
            result = session.exec(  # type: ignore[call-overload]
                update(Job)
                .where(Job.id == job.id, Job.status == JobStatus.AWAITING_CONFIRMATION)
                .values(status=JobStatus.REVIEWING, updated_at=datetime.now(UTC))
            )
            session.commit()
            if result.rowcount != 1:
                raise JobRejected("job_not_awaiting_confirmation", 409)
        self.executor.submit(self._run_review, job.id)

    def _finalize_redaction(self, job: Job) -> TaxReviewInput:
        regions = [RedactionRegion.model_validate(r) for r in job.redaction_log]
        original = self.storage.get(_key(job.id, ORIGINAL))
        self.storage.put(_key(job.id, REDACTED), self.pdf.redact(original, regions))

        words = [PdfWord(**w) for w in json.loads(self.storage.get(_key(job.id, WORDS)))]
        detection = json.loads(self.storage.get(_key(job.id, DETECTION)))
        parsed = PiiDetectionResult(
            regions=regions,
            word_tokens={int(k): v for k, v in detection["word_tokens"].items()},
            derived=detection["derived"],
        )
        masked = mask_words(words, regions, parsed.word_tokens)
        payload = build_review_input(
            masked, job.tax_year, job.questionnaire, parsed.derived, detection.get("unreadable_pages", [])
        )
        self.storage.put(_key(job.id, REVIEW_INPUT), payload.model_dump_json().encode())
        # The original and its extracted text are deleted as soon as redaction is confirmed (§8.4).
        for name in (ORIGINAL, WORDS, DETECTION):
            self.storage.delete(_key(job.id, name))
        return payload

    def _known_pii_values(self, job_id: str) -> list[str]:
        try:
            pii_map: dict[str, str] = json.loads(self.storage.get(_key(job_id, PII_MAP)))
        except FileNotFoundError:
            return []
        values: set[str] = set()
        for value in pii_map.values():
            for part in value.replace("/", " ").split():
                if len(part.strip(".,")) > 2:
                    values.add(part.strip(".,"))
        return sorted(values)

    def _run_review(self, job_id: str) -> None:
        try:
            with Session(self.engine) as session:
                job = session.get(Job, job_id)
                if job is None:
                    return
                session.expunge(job)
            payload = self._finalize_redaction(job)
            rules, rules_version = load_rules(payload.tax_year)
            response = self.llm.analyze(payload, rules)
            document = finalize_review(response.result, payload, rules, self._known_pii_values(job_id))
            with Session(self.engine) as session:
                if session.get(Job, job_id) is None:  # deleted by the user while we were working
                    self.storage.delete_prefix(_key(job_id))
                    return
                session.merge(
                    Report(
                        job_id=job_id,
                        model=response.model,
                        rules_version=rules_version,
                        findings=document.model_dump(mode="json"),
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                    )
                )
                session.commit()
            self._update(job_id, status=JobStatus.DONE)
        except LlmError as exc:
            _log_failure(job_id, "review", exc)
            self._mark_failed(job_id, exc.code)
        except PdfError as exc:
            _log_failure(job_id, "final redaction", exc)
            self._mark_failed(job_id, "pdf_timeout" if isinstance(exc, PdfTimeoutError) else "pdf_unreadable")
        except Exception as exc:
            _log_failure(job_id, "review", exc)
            self._mark_failed(job_id, "review_failed")

    # -- deletion and retention --------------------------------------------------

    def delete_job(self, job_id: str) -> None:
        """Files first, then rows (§8.4), so a failure never leaves files without a row pointing at them."""
        self.storage.delete_prefix(_key(job_id))
        with Session(self.engine) as session:
            report = session.get(Report, job_id)
            if report:
                session.delete(report)
            job = session.get(Job, job_id)
            if job:
                session.delete(job)
            session.commit()

    def cleanup_expired(self) -> int:
        now = datetime.now(UTC)
        with Session(self.engine) as session:
            expired = [job.id for job in session.exec(select(Job).where(Job.expires_at < now)).all()]
        deleted = 0
        for job_id in expired:
            try:
                self.delete_job(job_id)
                deleted += 1
            except Exception as exc:
                _log_failure(job_id, "retention cleanup", exc)
        if deleted:
            logger.info("retention cleanup deleted %d expired job(s)", deleted)
        return deleted

    def startup_recover(self) -> int:
        with Session(self.engine) as session:
            interrupted = session.exec(
                select(Job).where(Job.status.in_([JobStatus.REDACTING, JobStatus.REVIEWING]))  # type: ignore[attr-defined]
            ).all()
            for job in interrupted:
                job.status = JobStatus.FAILED
                job.error_code = "interrupted"
                job.updated_at = datetime.now(UTC)
                session.add(job)
            session.commit()
            return len(interrupted)

    def license_warning(self) -> str | None:
        settings = self.settings
        if (
            settings.auth_mode != AuthMode.NONE
            and settings.pdf_backend == PdfBackendName.PYMUPDF
            and settings.license_path_confirmed not in {"A", "B"}
        ):
            return (
                "PDF_BACKEND=pymupdf with network access enabled, but LICENSE_PATH_CONFIRMED is not A or B. "
                "Record the PyMuPDF licensing decision in LICENSING.md before anyone else uses the app (§4.8)."
            )
        return None

    # -- helpers -------------------------------------------------------------------

    def _update(self, job_id: str, **fields: Any) -> bool:
        """Returns False when the job no longer exists."""
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return False
            for name, value in fields.items():
                setattr(job, name, value)
            job.updated_at = datetime.now(UTC)
            session.add(job)
            session.commit()
            return True

    def _mark_failed(self, job_id: str, error_code: str) -> None:
        try:
            self._update(job_id, status=JobStatus.FAILED, error_code=error_code)
        except Exception as exc:  # the database itself may be the problem
            _log_failure(job_id, "marking failed", exc)

