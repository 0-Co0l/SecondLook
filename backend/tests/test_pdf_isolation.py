from __future__ import annotations

import pymupdf
import pytest

from app.models import Box
from app.pdf import PdfEncryptedError, PdfInvalidError, PdfTimeoutError, build_pdf_backend
from app.settings import PdfBackendName


def _pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Account 1234567890")
    return doc.tobytes()


@pytest.mark.parametrize("name", list(PdfBackendName))
def test_isolated_calls_work_and_fail_cleanly(name: PdfBackendName) -> None:
    backend = build_pdf_backend(name, 30)
    assert backend.page_count(_pdf()) == 1
    assert any(w.text == "1234567890" for w in backend.extract_words(_pdf()))
    with pytest.raises(PdfInvalidError):
        backend.page_count(b"%PDF-1.7 not really")
    locked = pymupdf.open()
    locked.new_page()
    with pytest.raises(PdfEncryptedError):
        backend.page_count(locked.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="a", owner_pw="b"))


def test_hung_parse_is_killed() -> None:
    backend = build_pdf_backend(PdfBackendName.PYMUPDF, 0.001)
    with pytest.raises(PdfTimeoutError):
        backend.redact(_pdf(), [Box(page=0, x0=0, y0=0, x1=10, y1=10)])


@pytest.mark.parametrize("name", list(PdfBackendName))
def test_redaction_is_destructive(name: PdfBackendName) -> None:
    backend = build_pdf_backend(name, 30, isolated=False)
    words = backend.extract_words(_pdf())
    target = next(w for w in words if w.text == "1234567890")
    out = backend.redact(_pdf(), [Box(page=0, x0=target.x0 - 1, y0=target.y0 - 1, x1=target.x1 + 1, y1=target.y1 + 1)])
    text = pymupdf.open(stream=out).load_page(0).get_text()
    assert "1234567890" not in text
