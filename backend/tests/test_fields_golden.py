"""Field mapping golden set: every synthetic variant must read back the values it was built with."""

from __future__ import annotations

import pytest
from conftest import requires_ocr
from synthetic import VARIANTS, make_return

from app.fields import build_review_input, mask_words
from app.jobs import extract_all_words
from app.models import Questionnaire
from app.pdf import build_pdf_backend
from app.pii import detect_pii
from app.settings import PdfBackendName


def _get(payload, path: str):  # type: ignore[no-untyped-def]
    if path == "dependents":
        return len(payload.dependents)
    if "." not in path:
        return getattr(payload, path)
    section, name = path.split(".")
    return getattr(getattr(payload, section), name)


@requires_ocr
@pytest.mark.slow
@pytest.mark.parametrize("backend_name", list(PdfBackendName))
@pytest.mark.parametrize("variant", VARIANTS)
def test_fields_match_expected(variant: str, backend_name: PdfBackendName) -> None:
    sample = make_return(variant, seed=7)
    backend = build_pdf_backend(backend_name, 120, isolated=False)
    words, unreadable = extract_all_words(backend, sample.pdf, backend.page_count(sample.pdf))
    detection, _ = detect_pii(words, 2025, "en_core_web_md")
    masked = mask_words(words, detection.regions, detection.word_tokens)
    payload = build_review_input(masked, 2025, Questionnaire().model_dump(), detection.derived, unreadable)

    mismatches = {path: (_get(payload, path), want) for path, want in sample.expected.items() if _get(payload, path) != want}
    assert mismatches == {}
    assert "1040" in payload.forms_present and "Schedule 1" in payload.forms_present
    assert all(d.age_bracket == "under 17" for d in payload.dependents)
    assert all(d.child_tax_credit_checked for d in payload.dependents)
    # Nothing in the LLM input may carry a seeded value.
    serialized = payload.model_dump_json().lower()
    assert not [v for v in sample.pii if len(v) > 3 and v.lower() in serialized]


def test_blank_amount_is_not_the_line_number() -> None:
    from app.pdf import PdfWord

    words = [
        PdfWord(0, t, x, 100, x + 20, 110)
        for t, x in [("12", 40), ("Standard", 60), ("deduction", 90), ("or", 120), ("itemized", 140),
                     ("deductions", 170), (".", 300), ("12", 458)]
    ]
    payload = build_review_input(words, 2025, {}, detection_derived())
    assert payload.deduction.amount is None


def test_two_amounts_on_one_row() -> None:
    from app.pdf import PdfWord

    texts = [("2a", 40), ("Tax-exempt", 60), ("interest", 110), ("2a", 200), ("21", 230), ("b", 260),
             ("Taxable", 280), ("interest", 330), (".", 380), ("2b", 458), ("1,310", 520)]
    words = [PdfWord(0, t, x, 100, x + 20, 110) for t, x in texts]
    payload = build_review_input(words, 2025, {}, detection_derived())
    assert payload.income.tax_exempt_interest == 21
    assert payload.income.taxable_interest == 1310


def detection_derived():  # type: ignore[no-untyped-def]
    from app.models import DerivedFacts

    return DerivedFacts()
