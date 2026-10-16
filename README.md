# SecondLook (Tax Return Review Aid)

SecondLook is a Python + React web app for AI-assisted review of U.S. individual federal tax returns.

## What it does
- Upload one PDF return (up to 25 MB / 100 pages)
- Redact detected PII before any LLM analysis
- Wait for explicit user confirmation before starting LLM review
- Generate findings report JSON plus downloadable HTML/Markdown
- Keep a local history and support immediate deletion

> Educational review aid only — not tax advice.

## Quick start (local development)

1. Copy env template:
   ```bash
   cp .env.example .env
   ```
2. Generate a Fernet key:
   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. Set in `.env`:
   - `FILE_ENCRYPTION_KEY` (use the generated key)
   - `ANTHROPIC_API_KEY` (optional for deterministic fallback mode)
4. Backend:
   ```bash
   cd backend
   pip install -e ".[dev]"
   uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
5. Frontend:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

## Container
- Build image from [Dockerfile](/Users/raymondvasquez/Dev/SecondLook/Dockerfile)
- Local profile with Caddy reverse proxy via [docker-compose.yml](/Users/raymondvasquez/Dev/SecondLook/docker-compose.yml)

## Key paths
- Backend app: [backend/app/](/Users/raymondvasquez/Dev/SecondLook/backend/app)
- Rules: [backend/rules/2025.json](/Users/raymondvasquez/Dev/SecondLook/backend/rules/2025.json)
- Frontend app: [frontend/src/](/Users/raymondvasquez/Dev/SecondLook/frontend/src)
- Licensing record: [LICENSING.md](/Users/raymondvasquez/Dev/SecondLook/LICENSING.md)
