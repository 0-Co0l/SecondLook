"""Tesseract OCR for pages with no usable text layer (§5.5 step 1). Runs locally; images never leave the machine."""

from __future__ import annotations

import re
from io import BytesIO

from .pdf import PdfWord, reading_to_display

OCR_DPI = 300
MIN_TEXT_WORDS = 5
# Low-confidence words are kept: dropping them could leave PII visible on the page unredacted.
MIN_CONFIDENCE = 0.0
TESSERACT_TIMEOUT_SECONDS = 120


def needs_ocr(text_layer_words: list[PdfWord], page: int) -> bool:
    return sum(1 for w in text_layer_words if w.page == page) < MIN_TEXT_WORDS


def _orientation(image: object) -> int:
    """Clockwise degrees the image must turn to be upright (0 when Tesseract can't tell)."""
    import pytesseract

    try:
        osd = pytesseract.image_to_osd(image, config="--psm 0", timeout=TESSERACT_TIMEOUT_SECONDS)
    except pytesseract.TesseractError:
        return 0
    match = re.search(r"Rotate:\s+(\d+)", osd)
    return int(match.group(1)) % 360 if match else 0


def ocr_page(png_bytes: bytes, page: int, dpi: int = OCR_DPI) -> list[PdfWord]:
    """Words with display-space boxes in PDF points, matching `PdfBackend.render_page` at `dpi`."""
    import pytesseract
    from PIL import Image

    image = Image.open(BytesIO(png_bytes))
    scale = 72 / dpi
    page_width, page_height = image.width * scale, image.height * scale
    turn = _orientation(image)
    upright = image.rotate(-turn, expand=True) if turn else image
    # The display is the upright page turned back by `turn`, i.e. turned (360 - turn) clockwise.
    rotation = (360 - turn) % 360

    data = pytesseract.image_to_data(
        upright,
        lang="eng",
        config="--psm 11",
        output_type=pytesseract.Output.DICT,
        timeout=TESSERACT_TIMEOUT_SECONDS,
    )
    words: list[PdfWord] = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][i])
        except (TypeError, ValueError):
            confidence = -1
        if confidence < MIN_CONFIDENCE:
            continue
        left, top, width, height = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        reading = (left * scale, top * scale, (left + width) * scale, (top + height) * scale)
        x0, y0, x1, y1 = reading_to_display(reading, rotation, page_width, page_height)
        words.append(PdfWord(page, text, x0, y0, x1, y1, "ocr", rotation, page_width, page_height))
    return words
