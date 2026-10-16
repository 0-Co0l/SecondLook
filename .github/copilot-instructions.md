# Tax Return Review App: Build Instructions (Python + React)

## 1. Purpose

Build a web application that lets a user upload a tax return PDF and get back an AI-assisted review. The app should:

1. **Redact** personally identifiable information (PII) from the PDF *before* anything is sent to the LLM.
2. **Submit** the redacted data to a large language model (LLM) to look for deductions and credits that may have been missed or under-claimed.
3. **Summarize** the findings in a clear, actionable report.

> **Positioning:** The app is an *educational review aid*, not tax preparation or tax advice. Every report must say so, and must recommend that users confirm findings with a qualified tax professional (CPA, EA, or tax attorney) before amending or filing.

---

## 2. Scope

### In scope (v1)
- U.S. federal individual returns (Form 1040 and its common schedules: 1, 2, 3, A, B, C, D, E, SE, 8812, 8863, 8880, 2441, 5695, 8962).
- Text-based PDFs (exported from tax software) and scanned/image PDFs (via OCR).
- A single PDF per review, up to 25 MB / 100 pages.
- Tax year selected by the user; defaults to the most recent completed tax year.
- Saved review history (job status and findings survive restarts).
- **Runs on a developer laptop, a rented server, an on-premises machine, or a cloud container platform from the same image** (see §4).

### Out of scope (v1)
- User accounts and sign-in (the design leaves a slot for them; see §4.6).
- State returns, business returns (1120, 1120-S, 1065), trusts, and estates.
- E-filing, amending, or generating corrected forms.
- Running more than one app instance at a time.

---

## 3. User Flow

1. **Landing page:** a plain-language explanation of what the app does, what gets redacted, what is sent to the LLM, and how long data is kept. The user checks a consent box before uploading.
2. **Upload:** drag-and-drop or file picker. The user selects the tax year and optionally answers a short questionnaire (see §6.3).
3. **Redaction preview:** show the redacted pages next to the originals, plus a list of what was redacted. The user can draw boxes to redact more, remove a false positive, and confirm.
   **Nothing is sent to the LLM until the user confirms.**
4. **Analysis:** a progress indicator while the review runs (the front end polls the job status).
5. **Report:** findings summary (see §7), downloadable as HTML or Markdown; the browser's Print → Save as PDF covers PDF output in v1.
6. **History:** a list of past reviews, each with "Delete now".
7. **Delete:** data is also auto-deleted after the retention window (see §8).

---

## 4. Architecture and Portability

### 4.1 Design goals
- **One container image runs everywhere.** Laptop, rented server, on-premises machine, or cloud container service. No code changes between them, only settings.
- **All settings come from environment variables** (or a `.env` file). Nothing provider-specific is hard-coded.
- **No cloud-only services are required.** Every external dependency has a self-hosted option.
- **Small footprint.** Runs comfortably in 2 vCPU / 4 GB RAM.
- **Two languages, one deployable.** Python on the server, TypeScript/React in the browser. The Python server also serves the built React files, so there is one container, one port, and no CORS setup.
- **No extra infrastructure in v1.** No Redis, no job queue service, no separate database server unless you choose one.

### 4.2 Structure

```
Browser: React app (static files built by Vite)
   │  HTTPS, JSON over REST
   ▼
(Optional) reverse proxy for HTTPS: Caddy on your own server, or the platform's built-in TLS
   ▼
One container: Python 3.12 + FastAPI (Uvicorn), port 8000
   ├── /api/*: REST endpoints (§9)
   ├── /healthz: health check for any host or load balancer
   ├── /*: serves the built React files
   ├── pdf.py: text + word positions, page images, true redaction (PyMuPDF)
   ├── ocr.py: Tesseract, only for pages with no text layer
   ├── pii.py: Presidio + IRS form-label anchors + regex
   ├── fields.py: map text to structured tax fields (Pydantic models)
   ├── review.py: build prompt, call LLM, validate, recompute $ in code
   ├── report.py: HTML / Markdown rendering
   ├── jobs.py: in-process worker pool (concurrent.futures)
   ├── db.py: SQLModel engine from DATABASE_URL (SQLite or PostgreSQL)
   ├── storage.py: StorageBackend interface (local folder or S3-compatible)
   ├── auth.py: AccessGuard interface (none / access code / OIDC)
   └── settings.py: all configuration, read from environment variables
```

### 4.3 Stack

| Layer | Choice | Why | License |
|---|---|---|---|
| Front end | **React + TypeScript**, built with **Vite** | Plain SPA; no server-side rendering needed | Free (MIT) |
| Web server / API | **FastAPI** + Uvicorn (Pydantic included) | Request validation and JSON schemas built in | Free (MIT/BSD) |
| Settings | **pydantic-settings** | Reads environment variables and `.env` into typed settings | Free (MIT) |
| Database access | **SQLModel** (SQLAlchemy) + **Alembic** migrations | Same code for SQLite and PostgreSQL | Free (MIT) |
| PDF handling | **PyMuPDF** | Word positions, page rendering, and *true* text-level redaction | **AGPL-3.0 or paid commercial license (see §4.8)** |
| OCR (scanned pages only) | **Tesseract** via `pytesseract` | Runs locally, so unredacted scans never leave the machine | Free (Apache 2.0) |
| PII detection | **Microsoft Presidio** (uses spaCy) | Finds names, addresses, and IDs; extendable | Free (MIT) |
| Background work | `concurrent.futures` (standard library) | Replaces Redis/Celery | — |
| File encryption | `cryptography` (Fernet) | Encrypts stored files | Free (Apache/BSD) |
| Object storage client (optional) | `boto3` | Works with any S3-compatible service | Free (Apache 2.0) |
| LLM | Claude API via the official `anthropic` Python SDK | Reachable from any host; zero-data-retention available | Pay per use |
| HTTPS on your own server | **Caddy** (in Docker Compose) | Gets and renews free Let's Encrypt certificates automatically | Free (Apache 2.0) |

### 4.4 Two deployment profiles, one image

The same image supports both profiles. Only environment variables change.

| | **Profile A: single machine** | **Profile B: managed platform** |
|---|---|---|
| Where | Laptop, rented server (VPS), on-premises machine | Container services such as Azure App Service, AWS App Runner/Lightsail, DigitalOcean App Platform, Render, Fly.io |
| Database | SQLite file on a persistent volume | Managed PostgreSQL |
| Files | Local folder on a persistent volume | S3-compatible object storage |
| HTTPS | Caddy container, or none on localhost | Provided by the platform |
| Secrets | `.env` file (permissions `600`) | Platform's secret settings, injected as environment variables |
| Monthly cost (excluding LLM) | $0 locally; about $5–25 on a VPS | About $25–90 |

**Rules that keep both profiles working:**
- **SQLite only on a local disk** with exactly one app instance. Never put SQLite on a network file share (SMB/NFS, including some platforms' "persistent storage" mounts); file locking there is unreliable. On such platforms use PostgreSQL.
- **The container must stay running.** Background jobs run inside the app process, so platforms that pause the CPU between requests or scale to zero won't work unless configured to keep one instance always on with CPU always allocated (e.g., Google Cloud Run with minimum instances = 1 and "CPU always allocated").
- **Pick a U.S. region** for hosting and storage, since the files are U.S. taxpayer documents.
- **Everything the app writes goes through `DATA_DIR`, the database, or the storage backend.** Nothing is written elsewhere in the container, so the container itself can be replaced at any time.

### 4.5 Configuration (environment variables)

| Variable | Example | Purpose |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | Interface to listen on. `0.0.0.0` only behind a proxy or platform |
| `APP_PORT` | `8000` | Port |
| `DATA_DIR` | `./data` | Root for the SQLite file and local storage |
| `DATABASE_URL` | `sqlite:///./data/app.db` or `postgresql+psycopg://user:pass@host/db` | Chooses SQLite or PostgreSQL |
| `STORAGE_BACKEND` | `local` or `s3` | Chooses local folder or object storage |
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | | Only when `STORAGE_BACKEND=s3` |
| `FILE_ENCRYPTION_KEY` | Fernet key | Encrypts stored files; generate once, keep safe |
| `ANTHROPIC_API_KEY` | | LLM access |
| `LLM_MODEL` | `claude-sonnet-5-5` | Model name |
| `LLM_INFERENCE_GEO` | `us` or `global` | Optional U.S.-only processing |
| `RETENTION_HOURS` | `168` in development, `24` in production | Auto-delete window |
| `AUTH_MODE` | `none`, `access_code`, or `oidc` | See §4.6 |
| `ACCESS_CODE` | | Only when `AUTH_MODE=access_code` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE` | | Only when `AUTH_MODE=oidc` |
| `SPACY_MODEL` | `en_core_web_md` | Presidio language model (§5.5) |
| `PDF_BACKEND` | `pymupdf` or `permissive` | PDF library choice (§4.8) |
| `LICENSE_PATH_CONFIRMED` | `A`, `B`, or empty | Records the PyMuPDF licensing decision (§4.8) |
| `WORKERS` | `2` | Background worker count |
| `LOG_LEVEL` | `info` | Logging level |

Provide a `.env.example` with every variable and safe defaults. Never commit `.env`.

### 4.6 Access control (no sign-in in v1)
All access checks go through one `AccessGuard` in `auth.py`, selected by `AUTH_MODE`:

- **`none` (default for development):** no checks. **The app must refuse to start in this mode unless `APP_HOST` is `127.0.0.1` or `localhost`**, so it can never be exposed to a network without protection by accident.
- **`access_code` (for a small pilot on a server):** users enter a shared code once; the server sets a signed, HTTP-only session cookie. Add per-IP rate limits on the code check and on uploads.
- **`oidc` (later):** validates tokens from any standard OpenID Connect provider (Microsoft Entra External ID, Auth0, Clerk, Keycloak, and others). No code outside `auth.py` and the React login screen changes.

Every job row has a `user_id` column. In `none` and `access_code` modes it's set to `"local"`; in `oidc` mode it's the signed-in user's ID. This lets sign-in be added later without changing tables.

### 4.7 Storage interface
`storage.py` defines `StorageBackend` with `put`, `get`, `delete`, `delete_prefix`, and `list_older_than`. Two implementations:

- **`LocalStorage`:** files under `DATA_DIR/jobs/{job_id}/`.
- **`S3Storage`:** any S3-compatible service: AWS S3, Cloudflare R2, Backblaze B2, DigitalOcean Spaces, Wasabi, or MinIO for on-premises. (Azure Blob Storage isn't S3-compatible; add an `AzureBlobStorage` class only if you choose Azure, which is a small, contained change.)

All files are encrypted with `FILE_ENCRYPTION_KEY` before they reach either backend, so the storage provider never sees readable content.

### 4.8 PyMuPDF licensing strategy

PyMuPDF is dual-licensed: **AGPL-3.0** (free) or a **commercial license** from Artifex (priced per project by quote; no public price list). AGPL's key condition is network use: if people other than the operator use the app over a network, its complete source code must be made available to them, even though no software is distributed.

#### Phase 1: Local development (now): use PyMuPDF under AGPL
- While the app runs only on the developer's machine (`AUTH_MODE=none`, `APP_HOST=127.0.0.1`), no one else interacts with it, so AGPL's source-sharing condition isn't triggered. Cost: $0.
- PyMuPDF's built-in true redaction gets the leak test (§11) passing fastest.

#### Required from day one: isolate PyMuPDF
- All PDF operations go through a `PdfBackend` interface in `pdf.py`:
  `extract_words(page)`, `render_page(page, dpi)`, `redact(regions) -> bytes`, `sanitize(doc) -> bytes`, `page_count()`.
- **No other module may import `fitz`/`pymupdf`.** Enforce this with a lint rule or a test that scans imports.
- Select the backend with a setting: `PDF_BACKEND=pymupdf` (default) or `PDF_BACKEND=permissive`.
- The leak test and golden-set tests must be written against the interface so they can run against either backend unchanged.

#### Phase 2: Decision gate (before anyone other than the developer uses the app)
This includes a small pilot on a rented server with `AUTH_MODE=access_code`. Before that happens, choose one path and record it in `LICENSING.md`:

| Path | When to choose it | Cost | What you must do |
|---|---|---|---|
| **A. Stay on AGPL and publish the source** | Internal tool, open-source project, or the code isn't a competitive asset | Free | License the app under AGPL-3.0; add a visible "Source code" link in the app footer pointing to the exact running version; keep the repository public and current. Note that the prompt, rules file and redaction logic become public. |
| **B. Buy a commercial license** | Closed-source commercial product and the quote is affordable | Artifex quote | Request a quote early (during Phase 1) so cost is known before launch; keep the license agreement on file. |
| **C. Switch to the permissive backend** | Closed source and Path B is too expensive, or you want to remove the licensing question entirely | Free | Implement `PermissivePdfBackend` (below), pass the full leak test with it, set `PDF_BACKEND=permissive`, and remove PyMuPDF from the production image's dependencies. |

**Default recommendation:** if the product will be private and commercial, request the Artifex quote during Phase 1. If it isn't clearly affordable, build `PermissivePdfBackend` before the pilot. Path C is always available as a fallback because of the interface.

#### Permissive backend (Path C)
- `pypdfium2` (Apache 2.0/BSD) renders pages and reads the text layer; `pdfplumber` (MIT) provides word bounding boxes; `Pillow` (HPND) paints redaction boxes and writes the output PDF.
- **Rasterize-and-rebuild redaction:** render each page to an image (300 DPI), paint the redaction boxes onto the pixels, and rebuild the PDF from the images. No text layer, metadata, form fields, attachments or scripts survive, so `sanitize()` is automatic.
- Trade-offs: the redacted PDF has no selectable text (irrelevant here, because the LLM receives structured JSON) and is larger on disk (irrelevant, because files expire).
- Must pass the same leak test (§11) at 100% before it can be used in production.

#### Checks
- CI fails if any module outside `pdf.py` imports PyMuPDF.
- If `PDF_BACKEND=pymupdf` and `AUTH_MODE` is not `none`, the app logs a startup warning unless `LICENSE_PATH_CONFIRMED=A` or `B` is set, as a reminder that the decision gate applies.
- Have counsel confirm the chosen path as part of the pre-launch legal review (§8.5). This section is guidance, not legal advice.

---

## 5. Step 1: Redaction

### 5.1 Principles
- **Redaction must be destructive.** Drawing black boxes over text is *not* redaction; the text remains extractable. With PyMuPDF, use `page.add_redact_annot()` followed by `page.apply_redactions()`.
- **Clean the whole file:** after redaction, remove metadata (`doc.set_metadata({})`, `doc.del_xml_metadata()`), embedded files, annotations, form fields and JavaScript, then save with `garbage=4, clean=True`.
- **Redact before the LLM, always.** The LLM only receives redacted, structured data, never the original file.
- **Preserve tax-relevant facts.** Replace PII that carries tax meaning with a *derived attribute*:

| PII | Redact as | Keep as derived attribute |
|---|---|---|
| Date of birth | `[DOB]` | Age bracket on Dec 31 of the tax year (`under 17`, `17–23`, `24–64`, `65+`) |
| Dependent names | `[DEPENDENT_1]`, … | Relationship, age bracket, months lived with taxpayer, student/disabled flags |
| Street address | `[ADDRESS]` | State only |
| Spouse name | `[SPOUSE]` | Filing status, age bracket, blind flag |
| Employer name/EIN | `[EMPLOYER_1]` | Wages, withholding, box 12/13 codes |
| Bank routing/account | `[BANK]` | Nothing |

### 5.2 What to redact
- SSNs and ITINs, EINs (with context checks so dollar amounts aren't matched)
- Names of taxpayer, spouse, dependents, preparers
- Street addresses, ZIP+4, foreign addresses
- Phone numbers and email addresses
- Dates of birth (convert per the table above)
- Bank routing and account numbers
- Preparer PTIN and firm EIN, Identity Protection PINs
- Signature areas
- Any user-marked regions from the preview step

### 5.3 What NOT to redact
- Dollar amounts, line numbers, form/schedule names
- Filing status checkboxes, tax year, generic form labels

### 5.4 Pseudonymization map
- Assign consistent tokens (`[TAXPAYER]`, `[DEPENDENT_1]`, …) so the LLM can reason about relationships.
- Store the token → real-value map **only as an encrypted file** in the storage backend, never in the database, logs, or LLM requests.
- The report keeps tokens by default; re-inserting first names is opt-in.

### 5.5 Detection pipeline
1. For each page, get words with bounding boxes (`page.get_text("words")`). If a page has almost no text, render it and run Tesseract (`image_to_data`) to get word boxes instead.
2. **Form-label anchors:** IRS forms have fixed labels ("Your social security number", "Home address", "Spouse's first name"). Find each label and treat the value next to it as PII.
3. **Presidio:** run its analyzer for names, locations, phone, email, and US SSN/ITIN/bank numbers. Add custom recognizers for EIN, PTIN, IP PIN, and ABA routing numbers (validate the checksum).
4. **Name propagation:** once a name is found, search every page for that same string (and the last name alone) and redact all occurrences.
5. Merge overlapping boxes, pad them slightly, and apply redactions.
6. Produce a redaction log (page, type, box, detector, score) for the preview screen. The log stores box positions and types only, never the redacted text.

**spaCy model size:** start with `en_core_web_md` (fits easily in a 4 GB machine). Switch to `en_core_web_lg` (~600 MB more memory) only if the leak test (§11) shows missed names.

---

## 6. Step 2: LLM Review

### 6.1 Input to the LLM
Send a structured JSON summary built by `fields.py` (as Pydantic models), not page text:

```json
{
  "tax_year": 2025,
  "filing_status": "married_filing_jointly",
  "state": "CA",
  "taxpayer": {"age_bracket": "24-64", "blind": false},
  "spouse": {"age_bracket": "24-64", "blind": false},
  "dependents": [
    {"id": "DEPENDENT_1", "relationship": "daughter", "age_bracket": "under 17", "months_in_home": 12}
  ],
  "income": {"wages": 112400, "taxable_interest": 310, "schedule_c_net": 8200},
  "adjustments": {"student_loan_interest": 0, "hsa_deduction": 0, "se_tax_deduction": 579},
  "deduction": {"type": "standard"},
  "credits_claimed": {"child_tax_credit": 2000, "child_dependent_care": 0, "education": 0},
  "payments": {"federal_withholding": 13250},
  "forms_present": ["1040", "Schedule 1", "Schedule C", "Schedule SE", "Schedule 8812"],
  "user_context": {"paid_childcare": true, "made_ira_contributions": "unknown"},
  "unreadable_fields": []
}
```

### 6.2 Tax-year rules
- Keep a versioned **rules file per tax year** (`rules/2025.json`) with standard deductions, credit phase-outs, contribution limits, and thresholds, each with its IRS source and a last-verified date. The file ships inside the image.
- Include the relevant rules in the prompt. **Do not rely on the model's memory for numbers.**
- A person reviews and updates the rules file whenever tax law changes and before each filing season.

### 6.3 Optional user questionnaire
Ask yes/no/unsure questions about things not visible on the return: childcare costs, college tuition, IRA/HSA contributions, energy-efficiency improvements or EV purchases, medical expenses, mortgage interest, charitable gifts, home office.

### 6.4 Prompt design
- Role: careful, conservative U.S. tax reviewer that cites the rule it relies on.
- Use only the provided rules file for numbers; say so if a needed rule is missing.
- Never invent figures; every finding must reference specific input fields.
- Classify each item as **likely missed**, **needs info**, **looks correct**, or **possible error** (including errors that would *increase* tax).
- No aggressive or unsupported positions.
- Output must match the schema in §6.5.
- Put the system prompt and rules file first and mark them for **prompt caching**; they're identical across users, which cuts their input cost by about 95% on repeat calls.

### 6.5 Required output schema
Define this as a Pydantic model and pass its JSON schema to the LLM:

```json
{
  "findings": [
    {
      "id": "F1",
      "category": "credit | deduction | adjustment | error | info",
      "title": "Child and Dependent Care Credit not claimed",
      "status": "likely_missed | needs_info | looks_correct | possible_error",
      "explanation": "Plain-language explanation.",
      "evidence_fields": ["user_context.paid_childcare", "credits_claimed.child_dependent_care"],
      "rule_reference": "rules/2025.json#child_dependent_care",
      "estimated_impact": {"low": 0, "high": 0, "basis": "how it was estimated"},
      "confidence": "high | medium | low",
      "questions_for_user": ["How much did you pay for care in 2025?"],
      "next_step": "Form/schedule to look at or document to gather"
    }
  ],
  "overall_notes": ""
}
```

### 6.6 Validation
- Parse the response with the Pydantic model; on failure, retry once with the validation error.
- Recompute dollar estimates in Python from the rules file where possible, and prefer the code's numbers.
- Reject findings that cite fields not present in the input.
- Run the PII detectors over the model's output before storing or showing it.

### 6.7 LLM provider requirements
- Zero data retention and no training on inputs, in a signed agreement.
- Calls made server-side only; the API key comes from `ANTHROPIC_API_KEY`, never from the React bundle.
- Put the LLM call behind an `LlmClient` interface so the provider or model can be swapped.
- Recommended model: a mid-tier model (e.g., Claude Sonnet 5.5). Test against the golden set (§11) before choosing.
- The app needs outbound HTTPS to the LLM API from wherever it's hosted; on-premises firewalls must allow it.

---

## 7. Step 3: Summary of Findings

### Structure
1. **Header:** tax year, filing status, review date, disclaimer.
2. **At a glance:** count of findings by status; estimated savings range from `likely_missed` items, shown separately from `needs_info`.
3. **Likely missed:** explanation, estimated impact, next step.
4. **Needs more information:** the questions and why they matter.
5. **Possible errors:** items that might increase tax or draw IRS attention.
6. **Looks correct:** a short list, so the user sees what was checked.
7. **What we couldn't read.**
8. **Next steps:** gather documents, talk to a professional, and a reminder that amended returns have deadlines (generally 3 years from filing or 2 years from payment, whichever is later; confirm with the IRS or a professional).
9. **Disclaimer.**

### Presentation rules
- Impact as a range, never a single exact number, with a confidence label.
- Plain language first, form/line references second.
- React renders the on-screen report from the findings JSON; the server produces the HTML and Markdown downloads from the same data.

---

## 8. Data Storage, Retention, and Security

### 8.1 Database tables (SQLModel)

**`jobs`**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID (string) | Random; also used in URLs |
| `user_id` | string | `"local"` until sign-in is added |
| `status` | enum | `uploaded`, `redacting`, `awaiting_confirmation`, `reviewing`, `done`, `failed` |
| `tax_year` | int | |
| `page_count` | int | |
| `redaction_log` | JSON | Box positions, types, detectors; no redacted text |
| `questionnaire` | JSON | Yes/no/unsure answers only |
| `error_code` | string, nullable | No error messages containing document text |
| `created_at`, `updated_at`, `expires_at` | datetime (UTC) | `expires_at` = created + `RETENTION_HOURS` |

**`reports`**
| Column | Type | Notes |
|---|---|---|
| `job_id` | UUID, FK → jobs | |
| `model` | string | LLM model used |
| `rules_version` | string | e.g., `2025@2026-02-10` |
| `findings` | JSON | Tokens only, never real names |
| `input_tokens`, `output_tokens` | int | For cost tracking |
| `created_at` | datetime (UTC) | |

Use JSON columns that work in both SQLite and PostgreSQL (SQLAlchemy `JSON` type). Manage all schema changes with Alembic.

### 8.2 What never goes in the database
The original PDF, redacted PDF, page images, extracted text, and the pseudonymization map. These live only as encrypted files in the storage backend under `jobs/{job_id}/`.

### 8.3 Restarts
On startup, mark any job left in `redacting` or `reviewing` as `failed` with `error_code = "interrupted"` and let the user retry. This keeps behavior correct on any host that restarts containers.

### 8.4 Retention
- Original deleted as soon as redaction is confirmed.
- A background task runs every 15 minutes and deletes jobs past `expires_at`: files first (`delete_prefix`), then database rows.
- "Delete now" removes a job immediately.
- Production `RETENTION_HOURS` should be 24; development may use longer.

### 8.5 Security
- **HTTPS** whenever the app is reachable from anything other than the same machine (Caddy or platform TLS).
- **Files encrypted at rest** with `FILE_ENCRYPTION_KEY`; also enable disk or bucket encryption where the host offers it.
- **Secrets** only in environment variables / `.env` (file permission `600`), never in the image or the repository.
- **Logging to stdout only** (any host can collect it). Never log file contents, extracted text, the PII map, or LLM prompts/responses; log job IDs, timings, and error codes. Make sure Uvicorn's access log doesn't record request bodies.
- **Uploads:** check the file signature (`%PDF-`), size and page limits; reject password-protected PDFs or ask for the password and decrypt in memory. Open PDFs in a worker process with a timeout so a malformed file can't hang the server.
- **Headers:** strict Content-Security-Policy; no third-party analytics.
- **Container:** run as a non-root user; read-only root filesystem except `DATA_DIR` and `/tmp`.
- **Server hosting (Profile A):** firewall allows only ports 80/443 (and SSH from your IP); automatic OS security updates on.
- **Development data:** use synthetic or sample returns until the redaction leak test passes.
- **Compliance review before launch:** IRC §7216 (if the operator is a tax return preparer), the FTC Safeguards Rule (which may require multi-factor sign-in once accounts are added), state privacy laws (e.g., CCPA/CPRA), and IRS Publication 4557.

---

## 9. API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Returns 200 when the app and database are reachable |
| POST | `/api/access` | Checks the access code (only in `access_code` mode) |
| POST | `/api/jobs` | Upload PDF + tax year + questionnaire; returns `job_id` |
| GET | `/api/jobs` | List the current user's jobs (history) |
| GET | `/api/jobs/{id}` | Job status (polled by React) |
| GET | `/api/jobs/{id}/pages/{n}?redacted=true` | Page image for the preview |
| GET | `/api/jobs/{id}/redactions` | Redaction log |
| PATCH | `/api/jobs/{id}/redactions` | Add/remove redaction regions |
| POST | `/api/jobs/{id}/confirm` | Confirm redaction; starts the LLM review |
| GET | `/api/jobs/{id}/report` | Findings JSON |
| GET | `/api/jobs/{id}/report.html`, `report.md` | Downloads |
| DELETE | `/api/jobs/{id}` | Delete all job data now |

Every `/api/jobs` route filters by the current `user_id`. Disable FastAPI's `/docs` page outside development.

---

## 10. Project Layout, Local Development, and Deployment

### 10.1 Layout

```
tax-review/
  Dockerfile                 # multi-stage: build React, then slim Python image + tesseract-ocr
  docker-compose.yml         # Profile A: app + Caddy + ./data volume
  Caddyfile                  # your domain → app:8000 (automatic HTTPS)
  .env.example
  LICENSING.md               # records the PyMuPDF licensing path (§4.8)
  backend/
    app/
      main.py  settings.py  db.py  storage.py  auth.py
      pdf.py                 # PdfBackend interface + PyMuPdfBackend / PermissivePdfBackend
      ocr.py  pii.py  fields.py  review.py  report.py  jobs.py
      models.py              # SQLModel tables + Pydantic schemas
    migrations/              # Alembic
    rules/2025.json
    tests/                   # pytest
    pyproject.toml
  frontend/
    src/pages/  Upload.tsx  Preview.tsx  Report.tsx  History.tsx
    src/api.ts
    package.json  vite.config.ts
```

### 10.2 Local development (no Docker needed)
1. Install Python 3.12, Node.js (LTS), and Tesseract (Homebrew, apt, or the Windows installer).
2. `cp .env.example .env`, generate a `FILE_ENCRYPTION_KEY`, and add your `ANTHROPIC_API_KEY`. Keep `AUTH_MODE=none`, `APP_HOST=127.0.0.1`, SQLite, and local storage.
3. Backend: install dependencies, run `alembic upgrade head`, then `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`.
4. Frontend: `npm run dev` (Vite on port 5173) with a proxy sending `/api` to port 8000.
5. `data/` and `.env` are in `.gitignore`.

### 10.3 Container image
- Multi-stage build: stage 1 runs `npm ci && npm run build`; stage 2 is `python:3.12-slim` with `tesseract-ocr` installed via apt, the Python dependencies, the spaCy model, and the built React files.
- Startup command runs `alembic upgrade head`, then Uvicorn.
- Runs as a non-root user; exposes port 8000; defines a `HEALTHCHECK` against `/healthz`.
- Build for both `linux/amd64` and `linux/arm64` (`docker buildx`), so it runs on Intel/AMD servers, ARM servers, and Apple Silicon machines.
- Push to any registry: GitHub Container Registry, Docker Hub, or your cloud's registry.

### 10.4 Deploying Profile A (VPS or on-premises)
1. Install Docker and the Compose plugin on a Linux machine (Ubuntu LTS recommended).
2. Copy `docker-compose.yml`, `Caddyfile`, and `.env` to the server; set `AUTH_MODE=access_code` (or `oidc` later) and `APP_HOST=0.0.0.0`.
3. Point your domain's DNS at the server; Caddy obtains the HTTPS certificate automatically.
4. `docker compose up -d`. Upgrades are `docker compose pull && docker compose up -d`.
5. Back up `./data/app.db` nightly with `sqlite3 app.db ".backup ..."` (it holds metadata and findings only); document files don't need backups since they expire.

On an internal network without a public domain, Caddy can use its internal certificate authority, or you can use your organization's certificates.

### 10.5 Deploying Profile B (managed platform)
1. Create a managed PostgreSQL database and an S3-compatible bucket (with a lifecycle rule deleting objects after a few days as a backstop).
2. Deploy the image as an always-on container with one instance and at least 2 GB RAM (4 GB recommended).
3. Set `DATABASE_URL`, `STORAGE_BACKEND=s3` and the `S3_*` settings, plus the other variables, in the platform's secret settings.
4. Point the platform's health check at `/healthz`.

### 10.6 Moving between profiles
Switching from Profile A to Profile B means: run Alembic against the new PostgreSQL database, copy `jobs` and `reports` rows across (or let old jobs expire), and change environment variables. No code changes.

---

## 11. Testing (pytest; Vitest for React if needed)

### Redaction (highest priority)
- A corpus of **synthetic** returns only (never real taxpayer data): text PDFs, scans, rotated pages, odd fonts.
- **Leak test:** for every output PDF, extract all text with PyMuPDF *and* OCR the rendered pages, and assert that no seeded PII value appears. Also check metadata, annotations, form fields, and attachments are empty. Must pass at 100% to release.
- Track per-type recall; target ≥ 99.5% for SSN/ITIN/bank numbers.
- **Database check:** after a full run, scan every database row and log line for the seeded PII values; none may appear.

### LLM review
- A golden set of synthetic returns with known missed items and known-correct returns.
- Track true positives, false positives, invented rules, and schema failures.
- Rerun whenever the prompt, model, or rules file changes.

### Portability
- Run the test suite against **both** SQLite and PostgreSQL, and **both** local storage and MinIO (an S3-compatible server that runs in Docker), in CI.
- Container smoke test: build the image, start it with Compose, upload a sample file, and check `/healthz` and a full review.
- Startup test: `AUTH_MODE=none` with `APP_HOST=0.0.0.0` must refuse to start.

### End to end
- Upload → preview → confirm → report → delete, then assert storage and database hold nothing for that job.

### Definition of Done (v1)
- [ ] All PDF code behind `PdfBackend`; CI check for stray PyMuPDF imports passes.
- [ ] PyMuPDF licensing path (A, B, or C) chosen and recorded in `LICENSING.md` before any pilot user (§4.8).
- [ ] Redaction leak test and database check pass on the full synthetic corpus.
- [ ] User must confirm redactions before any LLM call.
- [ ] LLM output validated; dollar estimates recomputed in code.
- [ ] Tax-year rules file present, sourced, and reviewed.
- [ ] Report includes disclaimer, ranges, confidence, and next steps.
- [ ] Data auto-deletes on schedule and on demand; verified by test.
- [ ] No PII in logs or the database; verified by test.
- [ ] Same image passes tests in Profile A and Profile B configurations.
- [ ] Privacy/legal review completed before anyone other than the developer uses it.

---

## 12. Hosting Options and Costs

Prices are approximate as of October 2026 and change often; confirm on each provider's pricing page.

### 12.1 Minimum machine
- **2 vCPU, 4 GB RAM, 20 GB disk** comfortably runs the app, OCR, and the `md` spaCy model. 2 GB RAM works for light use.

### 12.2 Where to run it

| Option | Profile | Approx. monthly cost | Notes |
|---|---|---|---|
| Your own computer | A | $0 | Development; `AUTH_MODE=none` on localhost |
| On-premises server | A | $0 beyond the hardware | Needs outbound HTTPS to the LLM API |
| Hetzner Cloud, 2 vCPU / 4 GB | A | ~ $5–10 | Lowest cost; U.S. locations available; you manage the server |
| DigitalOcean Droplet, 2 vCPU / 4 GB | A | ~ $24 | Simpler console, larger ecosystem; you manage the server |
| Azure App Service (Linux, P0v3, 4 GB) + PostgreSQL B1ms | B | ~ $60 + ~$15–20 | Managed; add storage (pennies) |
| Other container platforms (AWS App Runner/Lightsail, DigitalOcean App Platform, Render, Fly.io) | B | ~ $10–60 + database | Must keep one always-on instance |

### 12.3 Costs by layer

| Layer | Item | Cost |
|---|---|---|
| Software | React, Vite, Python, FastAPI, SQLModel, Alembic, Tesseract, Presidio, Caddy | **Free** |
| PDF | PyMuPDF (Phase 1, local development) | **Free** under AGPL |
| PDF | At the decision gate: Path A (AGPL + publish source) / Path B (Artifex commercial) / Path C (permissive backend) | Free / quote / free (§4.8) |
| Database | SQLite (Profile A) / managed PostgreSQL (Profile B) | Free / ~ $15–20 per month |
| File storage | Local disk / S3-compatible bucket | Free / pennies per month (files live ≤ 24 h) |
| HTTPS | Caddy + Let's Encrypt, or platform TLS | **Free** |
| Container registry | GitHub Container Registry or Docker Hub | Free tiers available |
| Domain | Any registrar | ~ $12 per year |
| Development tools | VS Code, Docker Desktop | VS Code free; Docker Desktop free for personal use and small businesses, paid for larger companies |
| LLM | Claude Sonnet 5.5: $2 per million input tokens, $10 per million output | ~ **$0.05 per review** (≈ 8K input + 3K output tokens) |
| LLM option | U.S.-only processing (`LLM_INFERENCE_GEO=us`) | +10% on token prices |
| LLM option | Prompt caching of system prompt + rules | Cached input billed at 5% of normal |

### 12.4 Example monthly totals

| Stage | Hosting | LLM | **Total** |
|---|---|---|---|
| Local development (≈ 100 test reviews) | $0 | ~$5 | **≈ $5/month** |
| Small pilot on a VPS (≈ 500 reviews) | ~$5–24 | ~$25 | **≈ $30–50/month** |
| Managed platform (≈ 1,000 reviews) | ~$75–80 | ~$50 | **≈ $125–130/month** |

These exclude a PyMuPDF commercial license, which applies only if Path B is chosen (§4.8). The biggest costs outside this table are people: legal/privacy review before launch, and someone keeping the tax rules file current each year.
