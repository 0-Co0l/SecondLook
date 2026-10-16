from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from .auth import AccessGuard, build_access_guard
from .db import init_db, session_scope
from .jobs import JobService
from .models import (
    AccessRequest,
    AccessResponse,
    CreateJobRequest,
    Job,
    JobResponse,
    JobStatus,
    PatchRedactionsRequest,
    Report,
)
from .pdf import RedactionRegion
from .settings import AuthMode, settings

logger = logging.getLogger("secondlook")
logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(docs_url="/docs" if settings.auth_mode == AuthMode.NONE else None)
guard: AccessGuard = build_access_guard()
jobs = JobService()
cleanup_task: asyncio.Task[None] | None = None


def get_user_id(request: Request) -> str:
    return guard.get_user_id(request)


@app.on_event("startup")
async def startup() -> None:
    global cleanup_task
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    jobs.startup_recover()
    warning = jobs.startup_warning()
    if warning:
        logger.warning(warning)
    cleanup_task = asyncio.create_task(_cleanup_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    global cleanup_task
    if cleanup_task:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        cleanup_task = None


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(15 * 60)
        jobs.cleanup_expired()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    with session_scope():
        pass
    return {"status": "ok"}


@app.post("/api/access", response_model=AccessResponse)
def access(payload: AccessRequest, response: Response) -> AccessResponse:
    if settings.auth_mode != AuthMode.ACCESS_CODE:
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="access code mode disabled")
    guard.login(response, payload.code)
    return AccessResponse(ok=True)


@app.post("/api/jobs")
async def create_job(
    metadata: Annotated[str, Form(...)],
    file: UploadFile = File(...),
    user_id: str = Depends(get_user_id),
) -> dict[str, str]:
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="file must be a PDF")
    raw = await file.read()
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file exceeds 25MB limit")
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="file signature is not PDF")
    try:
        page_count = jobs.pdf.page_count(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="failed to parse PDF") from exc
    if page_count > 100:
        raise HTTPException(status_code=400, detail="file exceeds 100 page limit")

    payload = CreateJobRequest.model_validate_json(metadata)
    now = datetime.now(UTC)
    job = Job(
        user_id=user_id,
        status=JobStatus.REDACTING,
        tax_year=payload.tax_year,
        questionnaire=payload.questionnaire.model_dump(mode="json"),
        created_at=now,
        updated_at=now,
        expires_at=jobs.next_expiry(),
    )
    with session_scope() as session:
        session.add(job)
        session.commit()
        session.refresh(job)

    jobs.store_original(job.id, raw)
    jobs.submit_redaction(job.id)
    return {"job_id": job.id}


@app.get("/api/jobs", response_model=list[JobResponse])
def list_jobs(user_id: str = Depends(get_user_id)) -> list[JobResponse]:
    with session_scope() as session:
        rows = session.exec(select(Job).where(Job.user_id == user_id).order_by(Job.created_at.desc())).all()
        return [JobResponse.model_validate(row.model_dump()) for row in rows]


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, user_id: str = Depends(get_user_id)) -> JobResponse:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="job not found")
        return JobResponse.model_validate(job.model_dump())


@app.get("/api/jobs/{job_id}/pages/{page_number}")
def get_page_preview(
    job_id: str,
    page_number: int,
    redacted: bool = True,
    user_id: str = Depends(get_user_id),
) -> Response:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="job not found")
        if page_number < 0 or page_number >= job.page_count:
            raise HTTPException(status_code=404, detail="page not found")
    source = jobs.get_redacted(job_id) if redacted else jobs.get_original(job_id)
    image = jobs.pdf.render_page(source, page_number)
    return Response(content=image, media_type="image/png")


@app.get("/api/jobs/{job_id}/redactions")
def get_redactions(job_id: str, user_id: str = Depends(get_user_id)) -> list[dict]:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="job not found")
        return job.redaction_log


@app.patch("/api/jobs/{job_id}/redactions")
def patch_redactions(job_id: str, patch: PatchRedactionsRequest, user_id: str = Depends(get_user_id)) -> list[dict]:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="job not found")
        current = [entry for entry in job.redaction_log if entry.get("id") not in patch.remove_ids]
        current.extend(patch.add)
        job.redaction_log = current
        job.updated_at = datetime.now(UTC)
        session.add(job)
        session.commit()
        return current


@app.post("/api/jobs/{job_id}/confirm")
def confirm_job(job_id: str, user_id: str = Depends(get_user_id)) -> dict[str, str]:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status != JobStatus.AWAITING_CONFIRMATION:
            raise HTTPException(status_code=409, detail="job is not awaiting confirmation")
        redacted = jobs.get_redacted(job_id)
        regions = [RedactionRegion(**entry) for entry in job.redaction_log]
        if regions:
            redacted = jobs.pdf.redact(redacted, regions)
            jobs.storage.put(f"jobs/{job.id}/redacted.pdf", redacted)
        jobs.storage.delete(f"jobs/{job.id}/original.pdf")
        job.status = JobStatus.REVIEWING
        job.updated_at = datetime.now(UTC)
        session.add(job)
        session.commit()
    jobs.submit_review(job_id)
    return {"status": "reviewing"}


@app.get("/api/jobs/{job_id}/report")
def get_report(job_id: str, user_id: str = Depends(get_user_id)) -> dict:
    with session_scope() as session:
        job = session.get(Job, job_id)
        report = session.get(Report, job_id)
        if not job or not report or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="report not found")
        return report.findings


@app.get("/api/jobs/{job_id}/report.md")
def get_report_markdown(job_id: str, user_id: str = Depends(get_user_id)) -> PlainTextResponse:
    with session_scope() as session:
        job = session.get(Job, job_id)
        report = session.get(Report, job_id)
        if not job or not report or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="report not found")
        markdown = jobs.render_report_markdown(report.findings, tax_year=job.tax_year, filing_status="unknown")
        return PlainTextResponse(markdown, media_type="text/markdown")


@app.get("/api/jobs/{job_id}/report.html")
def get_report_html(job_id: str, user_id: str = Depends(get_user_id)) -> Response:
    with session_scope() as session:
        job = session.get(Job, job_id)
        report = session.get(Report, job_id)
        if not job or not report or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="report not found")
        html = jobs.render_report_html(report.findings, tax_year=job.tax_year, filing_status="unknown")
        return Response(content=html, media_type="text/html")


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, user_id: str = Depends(get_user_id)) -> Response:
    with session_scope() as session:
        job = session.get(Job, job_id)
        report = session.get(Report, job_id)
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="job not found")
        jobs.storage.delete_prefix(f"jobs/{job.id}")
        if report:
            session.delete(report)
        session.delete(job)
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/admin/cleanup")
def cleanup(_: str = Depends(get_user_id)) -> dict[str, str]:
    jobs.cleanup_expired()
    return {"status": "ok"}


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="spa")


@app.exception_handler(HTTPException)
def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
