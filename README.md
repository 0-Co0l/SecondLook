# SecondLook (Tax Return Review Aid)

SecondLook is a Python + React web app that gives a U.S. federal individual return (Form 1040) an AI-assisted
second look. It redacts personal information first, shows the user exactly what was removed, and only after they
confirm sends a structured, redacted summary to Claude to look for missed deductions and credits and for possible
errors.

> Educational review aid only — not tax preparation or tax advice.

The full specification is in [.github/copilot-instructions.md](.github/copilot-instructions.md).

## How it works

```
Upload PDF ─► extract words (text layer, Tesseract for scanned pages) ─► detect PII
   (form-label anchors + regex + Presidio, name propagation) ─► redaction preview
   ─► user confirms ─► final redaction from the original, original deleted
   ─► structured fields (tokens instead of PII) ─► Claude review ─► validate,
   recompute $ from the rules file, scrub output ─► report (on screen, HTML, Markdown)
```

| Module | Role |
|---|---|
| `backend/app/pdf.py` | `PdfBackend` (PyMuPDF or permissive), each call in a child process with a timeout |
| `backend/app/ocr.py` | Tesseract for pages with no text layer, with orientation detection |
| `backend/app/pii.py` | PII detection, pseudonym tokens, derived attributes (age bracket, state) |
| `backend/app/fields.py` | Redacted words → `TaxReviewInput` |
| `backend/app/review.py` | Prompt, Claude call, validation, dollar recomputation |
| `backend/app/report.py` | HTML / Markdown downloads |
| `backend/app/jobs.py` | Job pipeline, retention, restart recovery |
| `backend/app/main.py` | FastAPI app factory and REST API; serves the built React app |
| `backend/rules/2025.json` | Tax-year rules with sources (**unverified — see below**) |

## Local development (no Docker needed)

Prerequisites: Python 3.12+, Node.js 22, and Tesseract with English data
(`brew install tesseract`, `apt install tesseract-ocr tesseract-ocr-eng`, or `pacman -S tesseract tesseract-data-eng`).

```bash
cp .env.example .env && chmod 600 .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# put that in FILE_ENCRYPTION_KEY; add ANTHROPIC_API_KEY, or set LLM_PROVIDER=fake to work offline

cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,pymupdf]"
python -m spacy download en_core_web_md
alembic upgrade head
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload
```

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to port 8000
```

`python -m app` is the production entry point: it runs migrations, then serves on `APP_HOST:APP_PORT`.

## Tests

```bash
cd backend && pytest -m "not slow"   # fast unit and API tests
cd backend && pytest                 # everything, including the redaction leak test (several minutes)
cd frontend && npm test
```

The leak test (`tests/test_redaction_leak.py`) runs a synthetic corpus — text, odd fonts, rotated pages, scans,
sideways scans, fillable forms — through both PDF backends and fails if any seeded PII value survives in the
text layer or in OCR of the output. It must pass at 100% before release. CI (`.github/workflows/ci.yml`) runs
the suite against SQLite + local storage and PostgreSQL + MinIO, then smoke-tests the container.

## Deployment

- **Profile A (single machine):** `mkdir -p data && sudo chown 10001 data`, set `DOMAIN`, `AUTH_MODE=access_code`
  and `ACCESS_CODE` in `.env`, then `docker compose up -d`. Caddy obtains the certificate. Back up
  `data/app.db` nightly with `sqlite3 data/app.db ".backup app-$(date +%F).db"`.
- **Profile B (managed platform):** deploy the image as one always-on instance with PostgreSQL (`DATABASE_URL`)
  and S3-compatible storage (`STORAGE_BACKEND=s3`, `S3_*`); point the health check at `/healthz`.

`AUTH_MODE=none` refuses to start unless `APP_HOST` is `127.0.0.1` or `localhost`.

## Before anyone else uses it

- [ ] Choose and record the PyMuPDF licensing path in [LICENSING.md](LICENSING.md).
- [ ] Verify every entry in `backend/rules/2025.json` against its cited IRS source, set `verified_at`, and mark the
      file `"review_status": "verified"`. The values were drafted without verification.
- [ ] Confirm zero data retention with Anthropic in a signed agreement (§6.7).
- [ ] Privacy and legal review (IRC §7216, FTC Safeguards Rule, state privacy laws, IRS Publication 4557).
