"""All PDF handling lives here, behind `PdfBackend` (§4.8).

No other module may import PyMuPDF; `tests/test_import_boundaries.py` enforces this.

Coordinates everywhere outside this module are PDF points with the origin at the top-left of the page
*as displayed* (page rotation applied), which is also the space of `render_page` images scaled by dpi/72.
Each backend converts to its library's native space internally.
"""

from __future__ import annotations

import multiprocessing
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from .models import Box
from .settings import PdfBackendName

REDACTION_DPI = 300


Box4 = tuple[float, float, float, float]


def display_to_reading(box: Box4, rotation: int, width: float, height: float) -> Box4:
    """Map a box from display space to reading space (text horizontal, left to right).

    `rotation` is how far the display is turned clockwise from reading orientation (a page's /Rotate);
    `width`/`height` are the display page size.
    """
    x0, y0, x1, y1 = box
    if rotation == 90:
        return y0, width - x1, y1, width - x0
    if rotation == 180:
        return width - x1, height - y1, width - x0, height - y0
    if rotation == 270:
        return height - y1, x0, height - y0, x1
    return box


def reading_to_display(box: Box4, rotation: int, width: float, height: float) -> Box4:
    x0, y0, x1, y1 = box
    if rotation == 90:
        return width - y1, x0, width - y0, x1
    if rotation == 180:
        return width - x1, height - y1, width - x0, height - y0
    if rotation == 270:
        return y0, height - x1, y1, height - x0
    return box


@dataclass(frozen=True)
class PdfWord:
    page: int
    text: str
    x0: float  # display space, used for redaction boxes
    y0: float
    x1: float
    y1: float
    source: str = "text"  # "text" (text layer) or "ocr"
    rotation: int = 0
    page_width: float = 0.0
    page_height: float = 0.0

    @property
    def reading_box(self) -> Box4:
        """Position in reading space, used for anything that depends on line and column layout."""
        return display_to_reading((self.x0, self.y0, self.x1, self.y1), self.rotation, self.page_width, self.page_height)


class PdfError(Exception):
    """The file could not be processed. Messages never contain document text."""


class PdfEncryptedError(PdfError):
    pass


class PdfInvalidError(PdfError):
    pass


class PdfTimeoutError(PdfError):
    pass


class PdfBackend(ABC):
    @abstractmethod
    def page_count(self, pdf_bytes: bytes) -> int:
        """Raises PdfEncryptedError for password-protected files, PdfInvalidError for unreadable ones."""

    @abstractmethod
    def page_sizes(self, pdf_bytes: bytes) -> list[tuple[float, float]]: ...

    @abstractmethod
    def extract_words(self, pdf_bytes: bytes) -> list[PdfWord]:
        """Words from the text layer, including filled-in form field values."""

    @abstractmethod
    def render_page(self, pdf_bytes: bytes, page_number: int, dpi: int = 144) -> bytes:
        """PNG of one page as displayed, including form field values."""

    @abstractmethod
    def redact(self, pdf_bytes: bytes, regions: Sequence[Box]) -> bytes:
        """Destructively remove everything under `regions`, then sanitize the whole file."""

    @abstractmethod
    def sanitize(self, pdf_bytes: bytes) -> bytes:
        """Remove metadata, embedded files, annotations, form fields, links and JavaScript."""


# ---------------------------------------------------------------------------
# PyMuPDF (AGPL-3.0 or commercial; see LICENSING.md)
# ---------------------------------------------------------------------------


class PyMuPdfBackend(PdfBackend):
    def __init__(self) -> None:
        try:
            import pymupdf
        except ImportError as exc:  # pragma: no cover - depends on the image
            raise RuntimeError("PDF_BACKEND=pymupdf but PyMuPDF is not installed") from exc
        self.pymupdf = pymupdf

    def _open(self, pdf_bytes: bytes) -> Any:
        try:
            doc = self.pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise PdfInvalidError("unreadable PDF") from exc
        if doc.needs_pass:
            doc.close()
            raise PdfEncryptedError("password-protected PDF")
        return doc

    def page_count(self, pdf_bytes: bytes) -> int:
        with self._open(pdf_bytes) as doc:
            return doc.page_count

    def page_sizes(self, pdf_bytes: bytes) -> list[tuple[float, float]]:
        with self._open(pdf_bytes) as doc:
            return [(page.rect.width, page.rect.height) for page in doc]

    def extract_words(self, pdf_bytes: bytes) -> list[PdfWord]:
        words: list[PdfWord] = []
        with self._open(pdf_bytes) as doc:
            for page_idx, page in enumerate(doc):
                to_display = page.rotation_matrix
                rotation, width, height = page.rotation % 360, page.rect.width, page.rect.height
                for x0, y0, x1, y1, text, *_ in page.get_text("words"):
                    rect = self.pymupdf.Rect(x0, y0, x1, y1) * to_display
                    words.append(
                        PdfWord(page_idx, text, rect.x0, rect.y0, rect.x1, rect.y1, "text", rotation, width, height)
                    )
        return words

    def render_page(self, pdf_bytes: bytes, page_number: int, dpi: int = 144) -> bytes:
        with self._open(pdf_bytes) as doc:
            if not 0 <= page_number < doc.page_count:
                raise PdfInvalidError("page out of range")
            return doc[page_number].get_pixmap(dpi=dpi, annots=True).tobytes("png")

    def redact(self, pdf_bytes: bytes, regions: Sequence[Box]) -> bytes:
        with self._open(pdf_bytes) as doc:
            # Turn form fields and annotations into ordinary page content so redaction can remove them.
            doc.bake(annots=True, widgets=True)
            for page_idx, page in enumerate(doc):
                page_regions = [r for r in regions if r.page == page_idx]
                if not page_regions:
                    continue
                to_unrotated = page.derotation_matrix
                for region in page_regions:
                    rect = self.pymupdf.Rect(region.x0, region.y0, region.x1, region.y1) * to_unrotated
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                page.apply_redactions(
                    images=self.pymupdf.PDF_REDACT_IMAGE_PIXELS,
                    graphics=self.pymupdf.PDF_REDACT_LINE_ART_NONE,
                    text=self.pymupdf.PDF_REDACT_TEXT_REMOVE,
                )
            return self._sanitize_open(doc)

    def sanitize(self, pdf_bytes: bytes) -> bytes:
        with self._open(pdf_bytes) as doc:
            doc.bake(annots=True, widgets=True)
            return self._sanitize_open(doc)

    def _sanitize_open(self, doc: Any) -> bytes:
        for page in doc:
            for widget in list(page.widgets()):
                page.delete_widget(widget)
            for annot in list(page.annots()):
                page.delete_annot(annot)
        doc.scrub(
            attached_files=True,
            clean_pages=True,
            embedded_files=True,
            hidden_text=True,
            javascript=True,
            metadata=True,
            redactions=True,
            remove_links=True,
            reset_fields=True,
            reset_responses=True,
            thumbnails=True,
            xml_metadata=True,
        )
        doc.set_metadata({})
        doc.del_xml_metadata()
        out = BytesIO()
        doc.save(out, garbage=4, clean=True, deflate=True, no_new_id=True)
        return out.getvalue()


# ---------------------------------------------------------------------------
# Permissive backend (Path C): pypdfium2 + pdfplumber + Pillow, rasterize-and-rebuild
# ---------------------------------------------------------------------------


class PermissivePdfBackend(PdfBackend):
    def __init__(self) -> None:
        import pdfplumber
        import pypdfium2

        self.pdfium = pypdfium2
        self.pdfplumber = pdfplumber

    def _open(self, pdf_bytes: bytes) -> Any:
        try:
            doc = self.pdfium.PdfDocument(pdf_bytes)
        except self.pdfium.PdfiumError as exc:
            if "password" in str(exc).lower():
                raise PdfEncryptedError("password-protected PDF") from exc
            raise PdfInvalidError("unreadable PDF") from exc
        doc.init_forms()
        return doc

    def page_count(self, pdf_bytes: bytes) -> int:
        doc = self._open(pdf_bytes)
        try:
            return len(doc)
        finally:
            doc.close()

    def page_sizes(self, pdf_bytes: bytes) -> list[tuple[float, float]]:
        doc = self._open(pdf_bytes)
        try:
            return [doc[i].get_size() for i in range(len(doc))]
        finally:
            doc.close()

    def extract_words(self, pdf_bytes: bytes) -> list[PdfWord]:
        self.page_count(pdf_bytes)  # surfaces encryption/corruption with the same errors as other calls
        words: list[PdfWord] = []
        with self.pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                layout = ("text", int(page.rotation or 0) % 360, float(page.width), float(page.height))
                for word in page.extract_words(keep_blank_chars=False, use_text_flow=False):
                    words.append(
                        PdfWord(page_idx, word["text"], word["x0"], word["top"], word["x1"], word["bottom"], *layout)
                    )
                words.extend(self._form_field_words(page_idx, page, layout))
        return words

    @staticmethod
    def _form_field_words(page_idx: int, page: Any, layout: tuple[str, int, float, float]) -> list[PdfWord]:
        """pdfplumber leaves form field values out of `extract_words`; lay them out across the widget box."""
        words: list[PdfWord] = []
        for annot in page.annots:
            value = (annot.get("data") or {}).get("V")
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            if not isinstance(value, str) or not value.strip() or value.startswith("/"):
                continue
            # Spread the value's words along the widget in reading direction, then map back to display space.
            tokens = value.split()
            total = sum(len(t) for t in tokens) + len(tokens) - 1
            _, rotation, page_w, page_h = layout
            rx0, ry0, rx1, ry1 = display_to_reading(
                (annot["x0"], annot["top"], annot["x1"], annot["bottom"]), rotation, page_w, page_h
            )
            cursor = 0
            for token in tokens:
                tx0 = rx0 + (rx1 - rx0) * cursor / total
                tx1 = rx0 + (rx1 - rx0) * (cursor + len(token)) / total
                x0, y0, x1, y1 = reading_to_display((tx0, ry0, tx1, ry1), rotation, page_w, page_h)
                words.append(PdfWord(page_idx, token, x0, y0, x1, y1, *layout))
                cursor += len(token) + 1
        return words

    def _render_pil(self, doc: Any, page_number: int, dpi: int) -> Any:
        page = doc[page_number]
        bitmap = page.render(scale=dpi / 72, may_draw_forms=True)
        return bitmap.to_pil().convert("RGB")

    def render_page(self, pdf_bytes: bytes, page_number: int, dpi: int = 144) -> bytes:
        doc = self._open(pdf_bytes)
        try:
            if not 0 <= page_number < len(doc):
                raise PdfInvalidError("page out of range")
            out = BytesIO()
            self._render_pil(doc, page_number, dpi).save(out, format="PNG")
            return out.getvalue()
        finally:
            doc.close()

    def redact(self, pdf_bytes: bytes, regions: Sequence[Box]) -> bytes:
        from PIL import ImageDraw

        scale = REDACTION_DPI / 72
        doc = self._open(pdf_bytes)
        output = self.pdfium.PdfDocument.new()
        try:
            # One page at a time keeps memory flat: a 300 DPI letter page is ~25 MB uncompressed.
            for page_number in range(len(doc)):
                image = self._render_pil(doc, page_number, REDACTION_DPI)
                draw = ImageDraw.Draw(image)
                for region in regions:
                    if region.page == page_number:
                        draw.rectangle(
                            [region.x0 * scale, region.y0 * scale, region.x1 * scale, region.y1 * scale], fill="black"
                        )
                single = BytesIO()
                image.save(single, format="PDF", resolution=REDACTION_DPI, quality=90)
                page_doc = self.pdfium.PdfDocument(single.getvalue())
                output.import_pages(page_doc)
                page_doc.close()
            out = BytesIO()
            output.save(out)
            return out.getvalue()
        finally:
            output.close()
            doc.close()

    def sanitize(self, pdf_bytes: bytes) -> bytes:
        # Rasterizing rebuilds the file from pixels only, so nothing else survives.
        return self.redact(pdf_bytes, [])


# ---------------------------------------------------------------------------
# Process isolation (§8.5): every call runs in a child process with a timeout, so a malformed or
# hostile file can't hang or crash the server.
# ---------------------------------------------------------------------------


def _make_backend(name: PdfBackendName) -> PdfBackend:
    if name == PdfBackendName.PERMISSIVE:
        return PermissivePdfBackend()
    return PyMuPdfBackend()


def _child_main(conn: Any, name: PdfBackendName, method: str, args: tuple[Any, ...]) -> None:
    try:
        result = getattr(_make_backend(name), method)(*args)
        conn.send(("ok", result))
    except PdfEncryptedError:
        conn.send(("encrypted", None))
    except PdfInvalidError:
        conn.send(("invalid", None))
    except BaseException as exc:  # noqa: BLE001 - only the type name crosses the boundary, never the message
        conn.send(("error", type(exc).__name__))
    finally:
        conn.close()


class IsolatedPdfBackend(PdfBackend):
    def __init__(self, name: PdfBackendName, timeout_seconds: float) -> None:
        self.name = name
        self.timeout_seconds = timeout_seconds
        self._ctx = multiprocessing.get_context("forkserver")
        self._ctx.set_forkserver_preload(["app.pdf"])

    def _call(self, method: str, *args: Any) -> Any:
        receiver, sender = self._ctx.Pipe(duplex=False)
        process = self._ctx.Process(target=_child_main, args=(sender, self.name, method, args), daemon=True)
        process.start()
        sender.close()
        try:
            if not receiver.poll(self.timeout_seconds):
                raise PdfTimeoutError(f"PDF operation '{method}' timed out")
            try:
                status, payload = receiver.recv()
            except EOFError as exc:
                raise PdfError(f"PDF worker for '{method}' exited unexpectedly") from exc
        finally:
            receiver.close()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()
        if status == "ok":
            return payload
        if status == "encrypted":
            raise PdfEncryptedError("password-protected PDF")
        if status == "invalid":
            raise PdfInvalidError("unreadable PDF")
        raise PdfError(f"PDF operation '{method}' failed ({payload})")

    def page_count(self, pdf_bytes: bytes) -> int:
        return self._call("page_count", pdf_bytes)

    def page_sizes(self, pdf_bytes: bytes) -> list[tuple[float, float]]:
        return self._call("page_sizes", pdf_bytes)

    def extract_words(self, pdf_bytes: bytes) -> list[PdfWord]:
        return self._call("extract_words", pdf_bytes)

    def render_page(self, pdf_bytes: bytes, page_number: int, dpi: int = 144) -> bytes:
        return self._call("render_page", pdf_bytes, page_number, dpi)

    def redact(self, pdf_bytes: bytes, regions: Sequence[Box]) -> bytes:
        plain = [Box(page=r.page, x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1) for r in regions]
        return self._call("redact", pdf_bytes, plain)

    def sanitize(self, pdf_bytes: bytes) -> bytes:
        return self._call("sanitize", pdf_bytes)


def build_pdf_backend(name: PdfBackendName, timeout_seconds: float, isolated: bool = True) -> PdfBackend:
    if isolated:
        return IsolatedPdfBackend(name, timeout_seconds)
    return _make_backend(name)

