"""Redaction leak test (§11, highest priority). Must pass at 100% for every backend before release.

For every synthetic return and every PdfBackend: run the real pipeline, then extract all text from the output
with PyMuPDF *and* OCR the rendered pages, and assert no seeded PII value appears. Also assert metadata,
annotations, form fields, embedded files and JavaScript are gone.
"""

from __future__ import annotations

import re
from collections import Counter

import pymupdf
import pytest
from conftest import requires_ocr
from synthetic import VARIANTS, make_return

from app.jobs import extract_all_words
from app.ocr import ocr_page
from app.pdf import build_pdf_backend
from app.pii import detect_pii
from app.settings import PdfBackendName

SEEDS = (1, 2, 3)
SPACY_MODEL = "en_core_web_md"
RECALL_COUNTS: Counter[tuple[str, bool]] = Counter()

pytestmark = [requires_ocr, pytest.mark.slow]


def _tokens(text: str) -> list[str]:
    return [t for t in (re.sub(r"[^a-z0-9@.]", "", raw.lower()).strip(".") for raw in text.split()) if t]


def _value_tokens(value: str) -> list[str]:
    return _tokens(value)


def _contains(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def _pii_kind(value: str) -> str:
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}", value):
        return "ssn"
    if value.isdigit() and len(value) >= 9:
        return "bank"
    if "@" in value:
        return "email"
    if not re.search(r"[A-Za-z]", value):
        return "number"
    return "text"


def _surviving_text(pdf: bytes, backend) -> list[str]:  # type: ignore[no-untyped-def]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = " ".join(page.get_text() for page in doc)
    pages = doc.page_count
    doc.close()
    ocr = " ".join(w.text for p in range(pages) for w in ocr_page(backend.render_page(pdf, p, 300), p))
    return _tokens(text + " " + ocr)


def _assert_sanitized(pdf: bytes, seeded: list[str]) -> None:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        metadata = {k: v for k, v in doc.metadata.items() if v}
        for field in ("title", "author", "subject", "keywords"):
            assert field not in metadata, f"metadata field {field} survived"
        assert not any(v.lower() in " ".join(metadata.values()).lower() for v in seeded if len(v) > 3)
        assert not doc.get_xml_metadata()
        assert doc.embfile_count() == 0
        assert not doc.is_form_pdf
        for page in doc:
            assert not list(page.annots()), "annotations survived"
            assert not list(page.widgets()), "form fields survived"
        for xref in range(1, doc.xref_length()):
            source = doc.xref_object(xref, compressed=False) or ""
            assert "/JavaScript" not in source and "/JS " not in source
    finally:
        doc.close()


@pytest.mark.parametrize("backend_name", list(PdfBackendName))
@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", SEEDS)
def test_no_seeded_pii_survives(variant: str, seed: int, backend_name: PdfBackendName) -> None:
    sample = make_return(variant, seed)
    backend = build_pdf_backend(backend_name, 120, isolated=False)
    words, _ = extract_all_words(backend, sample.pdf, backend.page_count(sample.pdf))
    detection, _ = detect_pii(words, 2025, SPACY_MODEL)
    redacted = backend.redact(sample.pdf, detection.regions)

    surviving = _surviving_text(redacted, backend)
    leaks = [value for value in sample.pii if _contains(surviving, _value_tokens(value))]
    for value in sample.pii:
        RECALL_COUNTS[(_pii_kind(value), value not in leaks)] += 1
    assert leaks == [], f"PII survived redaction ({variant}, seed {seed}, {backend_name}): {len(leaks)} value(s)"
    _assert_sanitized(redacted, sample.pii)


def test_recall_by_type() -> None:
    """Per-type recall over the whole corpus run above; SSN/ITIN and bank numbers must be ≥ 99.5% (§11)."""
    if not RECALL_COUNTS:
        pytest.skip("leak tests did not run")
    for kind in {k for k, _ in RECALL_COUNTS}:
        found, missed = RECALL_COUNTS[(kind, True)], RECALL_COUNTS[(kind, False)]
        recall = found / (found + missed)
        print(f"recall[{kind}] = {recall:.4f} ({found}/{found + missed})")
        if kind in {"ssn", "bank"}:
            assert recall >= 0.995
