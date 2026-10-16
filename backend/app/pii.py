from __future__ import annotations

import re
from dataclasses import dataclass

from .pdf import PdfWord, RedactionRegion

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EIN_PATTERN = re.compile(r"\b\d{2}-\d{7}\b")
EMAIL_PATTERN = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b")
PHONE_PATTERN = re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
BANK_PATTERN = re.compile(r"\b\d{9,17}\b")


@dataclass
class PiiDetection:
    kind: str
    detector: str
    score: float


def detect_word_pii(word: str) -> PiiDetection | None:
    for regex, kind in (
        (SSN_PATTERN, "ssn_or_itin"),
        (EIN_PATTERN, "ein"),
        (EMAIL_PATTERN, "email"),
        (PHONE_PATTERN, "phone"),
        (BANK_PATTERN, "bank_number"),
    ):
        if regex.search(word):
            return PiiDetection(kind=kind, detector="regex", score=0.95)
    return None


def detect_redactions(words: list[PdfWord]) -> list[RedactionRegion]:
    redactions: list[RedactionRegion] = []
    for idx, word in enumerate(words):
        detection = detect_word_pii(word.text)
        if not detection:
            continue
        redactions.append(
            RedactionRegion(
                id=f"r{idx}",
                page=word.page,
                x0=word.x0 - 1,
                y0=word.y0 - 1,
                x1=word.x1 + 1,
                y1=word.y1 + 1,
                kind=detection.kind,
                detector=detection.detector,
                score=detection.score,
            )
        )
    return redactions


def scrub_text(value: str) -> str:
    value = SSN_PATTERN.sub("[SSN]", value)
    value = EIN_PATTERN.sub("[EIN]", value)
    value = EMAIL_PATTERN.sub("[EMAIL]", value)
    value = PHONE_PATTERN.sub("[PHONE]", value)
    value = BANK_PATTERN.sub("[BANK]", value)
    return value
