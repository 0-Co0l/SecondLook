from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw

from .settings import PdfBackendName, settings


@dataclass
class PdfWord:
    page: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class RedactionRegion:
    id: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str
    detector: str
    score: float


class PdfBackend(ABC):
    @abstractmethod
    def page_count(self, pdf_bytes: bytes) -> int: ...

    @abstractmethod
    def extract_words(self, pdf_bytes: bytes) -> list[PdfWord]: ...

    @abstractmethod
    def render_page(self, pdf_bytes: bytes, page_number: int, dpi: int = 144) -> bytes: ...

    @abstractmethod
    def redact(self, pdf_bytes: bytes, regions: list[RedactionRegion]) -> bytes: ...

    @abstractmethod
    def sanitize(self, pdf_bytes: bytes) -> bytes: ...


class PyMuPdfBackend(PdfBackend):
    def __init__(self) -> None:
        try:
            import fitz  # type: ignore
        except Exception as exc:
            raise RuntimeError("PyMuPDF backend selected but fitz is unavailable") from exc
        self.fitz = fitz

    def page_count(self, pdf_bytes: bytes) -> int:
        with self.fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return len(doc)

    def extract_words(self, pdf_bytes: bytes) -> list[PdfWord]:
        words: list[PdfWord] = []
        with self.fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_idx, page in enumerate(doc):
                for raw in page.get_text("words"):
                    x0, y0, x1, y1, text, *_ = raw
                    words.append(PdfWord(page=page_idx, text=text, x0=x0, y0=y0, x1=x1, y1=y1))
        return words

    def render_page(self, pdf_bytes: bytes, page_number: int, dpi: int = 144) -> bytes:
        with self.fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc[page_number]
            pix = page.get_pixmap(dpi=dpi)
            return pix.tobytes("png")

    def redact(self, pdf_bytes: bytes, regions: list[RedactionRegion]) -> bytes:
        by_page: dict[int, list[RedactionRegion]] = {}
        for region in regions:
            by_page.setdefault(region.page, []).append(region)

        with self.fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_idx, region_list in by_page.items():
                page = doc[page_idx]
                for region in region_list:
                    rect = self.fitz.Rect(region.x0, region.y0, region.x1, region.y1)
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                page.apply_redactions()
            doc.set_metadata({})
            try:
                doc.del_xml_metadata()
            except Exception:
                pass
            out = BytesIO()
            doc.save(out, garbage=4, clean=True)
            return out.getvalue()

    def sanitize(self, pdf_bytes: bytes) -> bytes:
        with self.fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            doc.set_metadata({})
            try:
                doc.del_xml_metadata()
            except Exception:
                pass
            out = BytesIO()
            doc.save(out, garbage=4, clean=True)
            return out.getvalue()


class PermissivePdfBackend(PdfBackend):
    def __init__(self) -> None:
        self._fallback = PyMuPdfBackend()

    def page_count(self, pdf_bytes: bytes) -> int:
        return self._fallback.page_count(pdf_bytes)

    def extract_words(self, pdf_bytes: bytes) -> list[PdfWord]:
        return self._fallback.extract_words(pdf_bytes)

    def render_page(self, pdf_bytes: bytes, page_number: int, dpi: int = 144) -> bytes:
        return self._fallback.render_page(pdf_bytes, page_number, dpi=dpi)

    def redact(self, pdf_bytes: bytes, regions: list[RedactionRegion]) -> bytes:
        pages: list[Image.Image] = []
        page_total = self._fallback.page_count(pdf_bytes)
        for page_number in range(page_total):
            png = self._fallback.render_page(pdf_bytes, page_number, dpi=300)
            image = Image.open(BytesIO(png)).convert("RGB")
            draw = ImageDraw.Draw(image)
            for region in [r for r in regions if r.page == page_number]:
                draw.rectangle([region.x0, region.y0, region.x1, region.y1], fill="black")
            pages.append(image)

        out = BytesIO()
        first, rest = pages[0], pages[1:]
        first.save(out, format="PDF", save_all=True, append_images=rest)
        return out.getvalue()

    def sanitize(self, pdf_bytes: bytes) -> bytes:
        return self._fallback.sanitize(pdf_bytes)


def build_pdf_backend() -> PdfBackend:
    if settings.pdf_backend == PdfBackendName.PERMISSIVE:
        return PermissivePdfBackend()
    return PyMuPdfBackend()
