# syntax=docker/dockerfile:1.7
# Multi-stage build (§10.3): React build, then a slim Python image with Tesseract and the spaCy model.
# Build for both architectures with:
#   docker buildx build --platform linux/amd64,linux/arm64 -t <registry>/secondlook:<tag> --push .
# Path C (permissive PDF backend, no PyMuPDF in the image):
#   docker build --build-arg PDF_BACKEND=permissive -t secondlook:permissive .

FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ARG PDF_BACKEND=pymupdf
ARG SPACY_MODEL=en_core_web_md
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PDF_BACKEND=${PDF_BACKEND} \
    SPACY_MODEL=${SPACY_MODEL} \
    DATA_DIR=/app/data \
    DATABASE_URL=sqlite:////app/data/app.db \
    APP_PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/pyproject.toml backend/README.md ./
COPY backend/app ./app
RUN if [ "$PDF_BACKEND" = "pymupdf" ]; then pip install ".[pymupdf]"; else pip install .; fi \
    && python -m spacy download "${SPACY_MODEL}"

COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/rules ./rules
COPY --from=frontend /frontend/dist /app/frontend/dist

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown appuser /app/data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('APP_PORT', '8000'), timeout=4)"

# Runs `alembic upgrade head`, then Uvicorn on APP_HOST:APP_PORT.
CMD ["python", "-m", "app"]
