# PDF Backend Licensing Decision Record

This project supports two PDF backend modes:

- `PDF_BACKEND=pymupdf` (AGPL/commercial dual-license)
- `PDF_BACKEND=permissive` (rasterized fallback path)

Before any pilot or networked use by someone other than the developer, choose and document one path:

## Path A — AGPL with published source
- Keep `PDF_BACKEND=pymupdf`
- Publish complete corresponding source for the running service
- Set `LICENSE_PATH_CONFIRMED=A`

## Path B — Commercial PyMuPDF license
- Keep `PDF_BACKEND=pymupdf`
- Keep the signed Artifex agreement on file
- Set `LICENSE_PATH_CONFIRMED=B`

## Path C — Permissive backend
- Set `PDF_BACKEND=permissive`
- Remove PyMuPDF from production dependency/image plan
- Confirm leak tests pass against the permissive backend

Current decision: _not yet selected_.
