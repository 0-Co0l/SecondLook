"""Group positioned words into visual lines. Shared by PII detection and field mapping."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Protocol


class PositionedWord(Protocol):
    page: int
    text: str

    @property
    def reading_box(self) -> tuple[float, float, float, float]: ...


@dataclass
class Line:
    page: int
    indices: list[int]  # indices into the word list passed to `group_lines`
    text: str = ""
    spans: list[tuple[int, int]] = field(default_factory=list)  # char span of each word in `text`
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0

    def word_at_char(self, offset: int) -> int | None:
        for position, (start, end) in enumerate(self.spans):
            if start <= offset < end:
                return position
        return None

    def positions_in_span(self, start: int, end: int) -> list[int]:
        return [i for i, (s, e) in enumerate(self.spans) if s < end and e > start]


def group_lines(words: Sequence[PositionedWord]) -> list[Line]:
    """Words whose vertical centers (in reading space) are within half a typical word height share a line.

    Line coordinates are in reading space, so rotated pages group the same way as upright ones.
    """
    boxes = [w.reading_box for w in words]
    lines: list[Line] = []
    pages = sorted({w.page for w in words})
    for page in pages:
        page_indices = [i for i, w in enumerate(words) if w.page == page]
        if not page_indices:
            continue
        heights = [max(boxes[i][3] - boxes[i][1], 1.0) for i in page_indices]
        tolerance = max(median(heights) * 0.5, 2.0)
        page_indices.sort(key=lambda i: ((boxes[i][1] + boxes[i][3]) / 2, boxes[i][0]))
        current: list[int] = []
        current_center = 0.0
        for i in page_indices:
            center = (boxes[i][1] + boxes[i][3]) / 2
            if current and abs(center - current_center) > tolerance:
                lines.append(_finish(page, current, words, boxes))
                current = []
            current.append(i)
            current_center = sum((boxes[j][1] + boxes[j][3]) / 2 for j in current) / len(current)
        if current:
            lines.append(_finish(page, current, words, boxes))
    return lines


def _finish(
    page: int, indices: list[int], words: Sequence[PositionedWord], boxes: list[tuple[float, float, float, float]]
) -> Line:
    indices = sorted(indices, key=lambda i: boxes[i][0])
    line = Line(page=page, indices=indices)
    parts: list[str] = []
    offset = 0
    for i in indices:
        text = words[i].text
        line.spans.append((offset, offset + len(text)))
        parts.append(text)
        offset += len(text) + 1
    line.text = " ".join(parts)
    line.x0 = min(boxes[i][0] for i in indices)
    line.y0 = min(boxes[i][1] for i in indices)
    line.x1 = max(boxes[i][2] for i in indices)
    line.y1 = max(boxes[i][3] for i in indices)
    return line


_PUNCT = re.compile(r"^[^\w\[]+|[^\w\]]+$")


def normalize(text: str) -> str:
    """Lower-case and strip surrounding punctuation, for matching names across pages."""
    return _PUNCT.sub("", text).lower()
