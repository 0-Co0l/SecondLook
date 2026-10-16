from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlmodel import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartParser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import AccessGuard, RateLimiter, build_access_guard, client_ip
from .db import build_engine
from .jobs import JobRejected, JobService
from .models import (
    AccessRequest,
    AccessResponse,
    ConfigResponse,
    CreateJobRequest,
    Job,
    JobResponse,
    PatchRedactionsRequest,
)
from .pdf import PdfBackend, PdfError, build_pdf_backend
from .report import render_html, render_markdown
from .review import LlmClient, build_llm_client
from .settings import AuthMode, Settings, get_settings
from .storage import build_storage

logger = logging.getLogger("secondlook")

CLEANUP_INTERVAL_SECONDS = 15 * 60
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
# Downloaded HTML reports carry their own inline <style>.
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"


def configure_logging(level: str) -> None:
    """Logs go to stdout only (§8.5): job IDs, timings and error codes, never document content."""
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s", force=True)
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    for noisy in ("pdfminer", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Presidio warns once per non-English recognizer it skips at startup; nothing actionable.
    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)


class BodySizeLimitMiddleware:
    """Rejects request bodies over the limit before they are parsed, with or without Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                await _send_json(send, 413, {"error": "file_too_large"})
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(status_code=413, detail="file_too_large")
            return message

        await self.app(scope, limited_receive, send)


async def _send_json(send: Send, status_code: int, body: dict[str, Any]) -> None:
    import json

    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def create_app(
    settings: Settings | None = None,
    *,
    llm: LlmClient | None = None,
    pdf: PdfBackend | None = None,
    start_background_tasks: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    # Keep uploads in memory instead of an unencrypted temp file on disk (§8.2).
    MultiPartParser.spool_max_size = settings.max_upload_bytes + 1024 * 1024

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = build_engine(settings.database_url)
        service = JobService(
            settings=settings,
            engine=engine,
            storage=build_storage(settings),
            pdf=pdf or build_pdf_backend(settings.pdf_backend, settings.pdf_timeout_seconds),
            llm=llm or build_llm_client(settings),
        )
        app.state.engine = engine
        app.state.jobs = service
        app.state.guard = build_access_guard(settings)
        app.state.upload_limiter = RateLimiter(limit=20, window_seconds=60 * 60)

        recovered = service.startup_recover()
        if recovered:
            logger.warning("marked %d interrupted job(s) as failed", recovered)
        warning = service.license_warning()
        if warning:
            logger.warning(warning)

        cleanup_task: asyncio.Task[None] | None = None
        if start_background_tasks:
            cleanup_task = asyncio.create_task(_cleanup_loop(service))
        try:
            yield
        finally:
            if cleanup_task:
                cleanup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cleanup_task
            service.shutdown()
            engine.dispose()

    dev_docs = settings.auth_mode == AuthMode.NONE
    app = FastAPI(
        title="SecondLook",
        lifespan=lifespan,
        docs_url="/docs" if dev_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev_docs else None,
    )
    app.state.settings = settings
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + 64 * 1024)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes the submitted values back; keep errors generic.
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    @app.exception_handler(JobRejected)
    async def job_rejected(_: Request, exc: JobRejected) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code})

    @app.exception_handler(PdfError)
    async def pdf_error(_: Request, exc: PdfError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "pdf_unreadable"})

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, str]:
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.include_router(api_router)
    _mount_frontend(app)
    return app


async def _cleanup_loop(service: JobService) -> None:
    while True:
        try:
            await run_in_threadpool(service.cleanup_expired)
        except Exception as exc:  # keep the loop alive; the next run retries
            logger.error("retention cleanup failed (%s)", type(exc).__name__)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


def _mount_frontend(app: FastAPI) -> None:
    if not FRONTEND_DIST.is_dir():
        return
    index = FRONTEND_DIST / "index.html"

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str) -> Response:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not_found")
        candidate = (FRONTEND_DIST / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST):
            return FileResponse(candidate)
        # Client-side routes such as /report/<id> all serve the SPA shell.
        return FileResponse(index)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_jobs(request: Request) -> JobService:
    return request.app.state.jobs


def get_user_id(request: Request) -> str:
    guard: AccessGuard = request.app.state.guard
    return guard.get_user_id(request)


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
JobsDep = Annotated[JobService, Depends(get_jobs)]
UserDep = Annotated[str, Depends(get_user_id)]


def get_owned_job(job_id: str, session: SessionDep, jobs: JobsDep, user_id: UserDep) -> Job:
    """Every /api/jobs/{id} route filters by the current user (§9); other users' jobs look missing."""
    job = jobs.get_owned_job(session, job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return job


OwnedJob = Annotated[Job, Depends(get_owned_job)]


# ---------------------------------------------------------------------------
# API (§9)
# ---------------------------------------------------------------------------

api_router = APIRouter(prefix="/api")


@api_router.get("/config", response_model=ConfigResponse)
def config(settings: Annotated[Settings, Depends(get_app_settings)]) -> ConfigResponse:
    return ConfigResponse(
        auth_mode=settings.auth_mode.value,
        retention_hours=settings.retention_hours,
        default_tax_year=settings.default_tax_year,
        max_upload_mb=settings.max_upload_bytes // (1024 * 1024),
        max_pages=settings.max_pages,
        llm_model=settings.llm_model,
        source_code_url=settings.source_code_url or None,
    )


@api_router.post("/access", response_model=AccessResponse)
def access(payload: AccessRequest, request: Request, response: Response) -> AccessResponse:
    guard: AccessGuard = request.app.state.guard
    guard.login(request, response, payload.code)
    return AccessResponse(ok=True)


@api_router.post("/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(
    request: Request,
    jobs: JobsDep,
    user_id: UserDep,
    settings: Annotated[Settings, Depends(get_app_settings)],
    metadata: Annotated[str, Form(max_length=10_000)],
    file: Annotated[UploadFile, File()],
) -> dict[str, str]:
    request.app.state.upload_limiter.check(client_ip(request))
    if file.content_type not in {"application/pdf", "application/x-pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="not_a_pdf")
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="file_too_large")
    try:
        payload = CreateJobRequest.model_validate_json(metadata)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="invalid_metadata") from exc
    job = await run_in_threadpool(jobs.create_job, user_id, raw, payload)
    return {"job_id": job.id}


@api_router.get("/jobs", response_model=list[JobResponse])
def list_jobs(session: SessionDep, jobs: JobsDep, user_id: UserDep) -> list[JobResponse]:
    return [JobResponse.model_validate(job) for job in jobs.list_jobs(session, user_id)]


@api_router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job: OwnedJob) -> JobResponse:
    return JobResponse.model_validate(job)


@api_router.get("/jobs/{job_id}/pages/{page_number}")
def get_page_preview(job: OwnedJob, jobs: JobsDep, page_number: int, redacted: bool = True) -> Response:
    image = jobs.page_image(job, page_number, redacted)
    return Response(content=image, media_type="image/png", headers={"Cache-Control": "no-store"})


@api_router.get("/jobs/{job_id}/redactions")
def get_redactions(job: OwnedJob, jobs: JobsDep) -> dict[str, Any]:
    sizes = jobs.page_sizes(job) if job.status == "awaiting_confirmation" else []
    return {
        "regions": job.redaction_log,
        "pages": [{"width": w, "height": h} for w, h in sizes],
    }


@api_router.patch("/jobs/{job_id}/redactions")
def patch_redactions(job: OwnedJob, jobs: JobsDep, session: SessionDep, patch: PatchRedactionsRequest) -> dict[str, Any]:
    return {"regions": jobs.update_redactions(session, job, patch)}


@api_router.post("/jobs/{job_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
def confirm_job(job: OwnedJob, jobs: JobsDep) -> dict[str, str]:
    jobs.confirm(job)
    return {"status": "reviewing"}


def _report_or_404(session: Session, jobs: JobService, job: Job):  # type: ignore[no-untyped-def]
    document = jobs.report_document(session, job)
    if document is None:
        raise HTTPException(status_code=404, detail="report_not_found")
    return document


@api_router.get("/jobs/{job_id}/report")
def get_report(job: OwnedJob, jobs: JobsDep, session: SessionDep) -> dict[str, Any]:
    return _report_or_404(session, jobs, job).model_dump(mode="json")


@api_router.get("/jobs/{job_id}/report.md")
def get_report_markdown(job: OwnedJob, jobs: JobsDep, session: SessionDep) -> PlainTextResponse:
    document = _report_or_404(session, jobs, job)
    return PlainTextResponse(
        render_markdown(document, job.updated_at),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="tax-review-{job.tax_year}.md"'},
    )


@api_router.get("/jobs/{job_id}/report.html")
def get_report_html(job: OwnedJob, jobs: JobsDep, session: SessionDep) -> Response:
    document = _report_or_404(session, jobs, job)
    return Response(
        content=render_html(document, job.updated_at),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": REPORT_CSP,
            "Content-Disposition": f'inline; filename="tax-review-{job.tax_year}.html"',
        },
    )


@api_router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job: OwnedJob, jobs: JobsDep) -> Response:
    jobs.delete_job(job.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
