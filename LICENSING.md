# PDF Backend Licensing Decision Record

PyMuPDF is dual-licensed: AGPL-3.0 or a commercial license from Artifex. AGPL's network-use condition applies
as soon as anyone other than the operator uses the app over a network, including a small pilot with
`AUTH_MODE=access_code`. See §4.8 of `.github/copilot-instructions.md`.

## What the code supports

| | `PDF_BACKEND=pymupdf` | `PDF_BACKEND=permissive` |
|---|---|---|
| Libraries | PyMuPDF | pypdfium2 (Apache-2.0/BSD), pdfplumber (MIT), Pillow (HPND) |
| Redaction | True text-level redaction (`apply_redactions`), then scrub | Rasterize at 300 DPI, paint boxes, rebuild from images |
| Output | Searchable PDF | Image-only PDF (no text layer, metadata, forms or scripts survive) |
| Leak test (§11) | Passes on the synthetic corpus | Passes on the synthetic corpus |
| Image | `docker build .` | `docker build --build-arg PDF_BACKEND=permissive .` (PyMuPDF not installed) |

All PDF access goes through `PdfBackend` in `backend/app/pdf.py`; `tests/test_import_boundaries.py` fails CI if
any other module imports PyMuPDF. The leak test runs against both backends unchanged.

The app logs a startup warning when `PDF_BACKEND=pymupdf`, `AUTH_MODE` is not `none`, and
`LICENSE_PATH_CONFIRMED` is not `A` or `B`.

## Decision (required before anyone other than the developer uses the app)

Choose one path and record it below.

### Path A — AGPL with published source
- Keep `PDF_BACKEND=pymupdf`.
- License the app under AGPL-3.0 and keep the repository public and current.
- Set `SOURCE_CODE_URL` to the exact running version (e.g. a tag or commit URL); the app footer shows it.
- Note that the prompt, rules file and redaction logic become public.
- Set `LICENSE_PATH_CONFIRMED=A`.

### Path B — Commercial PyMuPDF license
- Request an Artifex quote early; keep the signed agreement on file.
- Keep `PDF_BACKEND=pymupdf` and set `LICENSE_PATH_CONFIRMED=B`.

### Path C — Permissive backend
- Build the image with `--build-arg PDF_BACKEND=permissive` (sets `PDF_BACKEND=permissive`, omits PyMuPDF).
- Confirm the full leak test passes against the permissive backend on the current corpus.

Have counsel confirm the chosen path as part of the pre-launch legal review. This record is guidance, not legal
advice.

**Current decision:** _not yet selected_ (Phase 1, local development only).
