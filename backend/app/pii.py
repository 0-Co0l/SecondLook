"""PII detection (§5.5): form-label anchors + regex + Presidio, name propagation, box merging.

Nothing here logs or returns document text except the pseudonymization map, which the caller must
store only as an encrypted file (§5.4).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from .layout import Line, group_lines, normalize
from .models import AgeBracket, DerivedDependent, DerivedFacts, PiiDetectionResult, RedactionRegion
from .pdf import PdfWord, reading_to_display

PAD = 1.5

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
SSN_SPACED_PATTERN = re.compile(r"\b\d{3} \d{2} \d{4}\b")
EIN_PATTERN = re.compile(r"\b\d{2}-\d{7}\b")
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]\d{4}(?!\d)")
PTIN_PATTERN = re.compile(r"\bP\d{8}\b")
ZIP4_PATTERN = re.compile(r"\b\d{5}-\d{4}\b")
LONG_DIGITS_PATTERN = re.compile(r"^\d{9,17}$")
DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
CITY_STATE_ZIP_PATTERN = re.compile(r"\b([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+)*),? ([A-Z]{2}) (\d{5}(?:-\d{4})?)\b")
MONEY_PATTERN = re.compile(r"^\(?-?\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\)?\.?$")

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR", "GU", "VI",
}

# Words that appear in IRS form text and must never be treated as names (they would otherwise be
# propagated across every page and mask labels the field mapper needs).
FORM_VOCABULARY = {
    "form", "schedule", "line", "lines", "total", "amount", "income", "tax", "taxes", "credit", "credits",
    "deduction", "deductions", "wages", "attach", "add", "subtract", "enter", "see", "instructions", "yes", "no",
    "you", "your", "spouse", "spouse's", "name", "names", "first", "last", "middle", "initial", "address", "home",
    "number", "street", "city", "town", "state", "zip", "code", "foreign", "country", "province", "postal", "apt",
    "social", "security", "filing", "status", "single", "married", "jointly", "separately", "head", "household",
    "qualifying", "surviving", "dependents", "dependent", "child", "children", "relationship", "presidential",
    "election", "campaign", "digital", "assets", "standard", "itemized", "refund", "owe", "payments", "sign",
    "here", "signature", "date", "occupation", "phone", "email", "preparer", "preparer's", "paid", "firm",
    "firm's", "ein", "ptin", "use", "only", "self-employed", "check", "if", "department", "treasury",
    "internal", "revenue", "service", "irs", "omb", "individual", "return", "u.s.", "us", "federal",
    "routing", "account", "type", "checking", "savings", "direct", "deposit", "identity", "protection", "pin",
    "employer", "employer's", "employee", "employee's", "payer", "payer's", "recipient", "recipient's",
    "business", "profit", "loss", "net", "gross", "adjusted", "taxable", "interest", "dividends", "ordinary",
    "qualified", "pensions", "annuities", "ira", "distributions", "benefits", "capital", "gain", "other",
    "schedule's", "jr", "sr", "ii", "iii", "the", "and", "or", "of", "for", "to", "from", "on", "in", "a", "an",
    "blind", "born", "before", "january", "age", "were", "was", "are", "is", "box", "boxes", "w-2", "1099",
    "son", "daughter", "stepchild", "foster", "brother", "sister", "grandchild", "niece", "nephew", "parent",
    "mother", "father", "credit's", "mfs", "hoh", "qss", "student", "disabled", "lived", "more", "than", "half",
    "nonresident", "alien", "health", "care", "education", "american", "opportunity", "earned", "premium",
    "estimated", "withheld", "withholding", "overpaid", "applied", "penalty", "third", "party", "designee",
    "designee's", "under", "penalties", "perjury", "declare", "examined", "knowledge", "belief", "true",
    "correct", "complete", "joint", "both", "must", "keep", "copy", "records", "page", "cat", "no.",
}

RELATIONSHIPS = {
    "son", "daughter", "stepson", "stepdaughter", "stepchild", "foster", "brother", "sister", "half-brother",
    "half-sister", "grandchild", "grandson", "granddaughter", "niece", "nephew", "parent", "mother", "father",
    "grandparent", "aunt", "uncle", "other",
}

LOCATION_ALLOWLIST = {
    "united states", "u.s.", "us", "usa", "america", "puerto rico", "guam", "american samoa", "virgin islands",
}


def aba_checksum_ok(digits: str) -> bool:
    if len(digits) != 9 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def is_money(text: str) -> bool:
    return bool(MONEY_PATTERN.match(text))


def looks_like_name_word(text: str) -> bool:
    cleaned = normalize(text)
    if len(cleaned) < 2 or not cleaned.replace("-", "").replace("'", "").isalpha():
        return False
    if cleaned in FORM_VOCABULARY:
        return False
    return text[:1].isupper()


def age_bracket(born: date, tax_year: int) -> AgeBracket:
    end = date(tax_year, 12, 31)
    age = end.year - born.year - ((end.month, end.day) < (born.month, born.day))
    if age < 0:
        return "unknown"
    if age < 17:
        return "under 17"
    if age <= 23:
        return "17-23"
    if age <= 64:
        return "24-64"
    return "65+"


def parse_date(text: str) -> date | None:
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Form-label anchors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    pattern: re.Pattern[str]
    kind: str
    role: str | None = None  # pseudonym role for person names
    inherit_role: bool = False  # "Last name" takes the role of the name label before it on the same line
    max_words: int = 6
    lines_below: int = 1


def _a(pattern: str, kind: str, **kwargs: object) -> Anchor:
    return Anchor(re.compile(pattern, re.IGNORECASE), kind, **kwargs)  # type: ignore[arg-type]


ANCHORS: tuple[Anchor, ...] = (
    _a(r"if joint return, spouse'?s first name(?: and middle initial)?", "name", role="SPOUSE"),
    _a(r"your first name(?: and middle initial)?", "name", role="TAXPAYER"),
    _a(r"(?<!first name and middle initial )\blast name", "name", inherit_role=True),
    _a(r"spouse'?s social security number", "ssn"),
    _a(r"your social security number", "ssn"),
    _a(r"home address \(number and street\)", "address", max_words=8),
    _a(r"apt\.? no\.?", "address", max_words=2),
    _a(r"city, town,? or post office", "address", max_words=5),
    _a(r"\bzip code\b", "zip", max_words=1),
    _a(r"foreign country name", "address"),
    _a(r"foreign province/state/county", "address"),
    _a(r"foreign postal code", "address", max_words=2),
    _a(r"spouse'?s date of birth", "dob", role="SPOUSE"),
    _a(r"(?:your )?date of birth|\bdob\b|birth date", "dob", role="TAXPAYER"),
    _a(r"routing number", "bank", max_words=1),
    _a(r"account number", "bank", max_words=1),
    _a(r"phone (?:no\.?|number)", "phone", max_words=3),
    _a(r"email address", "email", max_words=1),
    _a(r"identity protection pin", "ip_pin", max_words=1),
    _a(r"preparer'?s name", "name", role="PREPARER"),
    _a(r"\bptin\b", "ptin", max_words=1),
    _a(r"firm'?s name", "name", role="FIRM"),
    _a(r"firm'?s ein", "ein", max_words=1),
    _a(r"firm'?s address", "address", max_words=10),
    _a(r"employer'?s name, address,? and zip code", "employer", role="EMPLOYER", max_words=8, lines_below=3),
    _a(r"payer'?s name, street address", "employer", role="PAYER", max_words=8, lines_below=3),
    _a(r"employee'?s first name and initial", "name", role="TAXPAYER"),
    _a(r"employee'?s address and zip code", "address", max_words=8, lines_below=2),
    _a(r"employee'?s social security number", "ssn", max_words=1),
    _a(r"employer identification number", "ein", max_words=1),
    _a(r"recipient'?s name", "name", role="TAXPAYER"),
)

TABLE_HEADER = re.compile(r"relationship", re.IGNORECASE)
SIGNATURE_ANCHOR = re.compile(r"(your|spouse'?s) signature", re.IGNORECASE)
DEPENDENTS_HEADER = re.compile(r"first name|social security number", re.IGNORECASE)
DEPENDENTS_START = re.compile(r"^dependents\b|\bdependents \(see instructions\)", re.IGNORECASE)
# Income lines start with a line number ("1a", "2b", "7"); dependent rows start with a name.
DEPENDENTS_END = re.compile(
    r"^\s*\d{1,2}[a-z]?\b|total amount from form|^income\b|if more than four dependents", re.IGNORECASE
)

TOKEN_BY_KIND = {
    "ssn": "[SSN]",
    "ein": "[EIN]",
    "address": "[ADDRESS]",
    "zip": "[ADDRESS]",
    "phone": "[PHONE]",
    "email": "[EMAIL]",
    "dob": "[DOB]",
    "bank": "[BANK]",
    "ptin": "[PTIN]",
    "ip_pin": "[IP_PIN]",
    "signature": "[SIGNATURE]",
    "location": "[ADDRESS]",
    "other": "[REDACTED]",
}
GENERIC_TOKENS = set(TOKEN_BY_KIND.values())


# ---------------------------------------------------------------------------
# Presidio (loaded once per process; spaCy model size from SPACY_MODEL)
# ---------------------------------------------------------------------------

_analyzer_lock = threading.Lock()
_analyzers: dict[str, object] = {}

# spaCy labels we never use; ignoring them also silences Presidio's per-call mapping warnings.
SPACY_LABELS_TO_IGNORE = [
    "CARDINAL", "ORDINAL", "QUANTITY", "MONEY", "PERCENT", "DATE", "TIME", "LAW", "LANGUAGE", "PRODUCT",
    "EVENT", "WORK_OF_ART", "FAC", "NORP", "ORG",
]

PRESIDIO_ENTITIES = [
    "PERSON",
    "LOCATION",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "US_SSN",
    "US_ITIN",
    "US_BANK_NUMBER",
    "US_EIN",
    "US_PTIN",
    "US_IP_PIN",
    "ABA_ROUTING",
]

PRESIDIO_KIND = {
    "PERSON": "name",
    "LOCATION": "location",
    "PHONE_NUMBER": "phone",
    "EMAIL_ADDRESS": "email",
    "US_SSN": "ssn",
    "US_ITIN": "ssn",
    "US_BANK_NUMBER": "bank",
    "US_EIN": "ein",
    "US_PTIN": "ptin",
    "US_IP_PIN": "ip_pin",
    "ABA_ROUTING": "bank",
}


def get_analyzer(spacy_model: str) -> object:
    with _analyzer_lock:
        if spacy_model not in _analyzers:
            _analyzers[spacy_model] = _build_analyzer(spacy_model)
        return _analyzers[spacy_model]


def _build_analyzer(spacy_model: str) -> object:
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    class AbaRoutingRecognizer(PatternRecognizer):
        def validate_result(self, pattern_text: str) -> bool:
            return aba_checksum_ok(pattern_text)

    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": spacy_model}],
            "ner_model_configuration": {"labels_to_ignore": SPACY_LABELS_TO_IGNORE},
        }
    ).create_engine()
    registry = RecognizerRegistry(supported_languages=["en"])
    registry.load_predefined_recognizers(languages=["en"], nlp_engine=nlp_engine)
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="US_EIN",
            patterns=[Pattern("ein", r"\b\d{2}-\d{7}\b", 0.6)],
            context=["ein", "employer", "identification"],
        )
    )
    registry.add_recognizer(
        PatternRecognizer(supported_entity="US_PTIN", patterns=[Pattern("ptin", r"\bP\d{8}\b", 0.7)], context=["ptin"])
    )
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="US_IP_PIN",
            patterns=[Pattern("ip_pin", r"\b\d{6}\b", 0.05)],
            context=["identity", "protection", "pin"],
        )
    )
    registry.add_recognizer(
        AbaRoutingRecognizer(
            supported_entity="ABA_ROUTING",
            patterns=[Pattern("aba", r"\b[0-3]\d{8}\b", 0.3)],
            context=["routing", "rtn", "aba"],
        )
    )
    return AnalyzerEngine(nlp_engine=nlp_engine, registry=registry, supported_languages=["en"])


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass
class _Hit:
    kind: str
    detector: str
    score: float
    token: str


@dataclass
class _State:
    words: Sequence[PdfWord]
    lines: list[Line]
    tax_year: int
    hits: dict[int, _Hit] = field(default_factory=dict)
    extra_boxes: list[RedactionRegion] = field(default_factory=list)
    persons: dict[str, list[list[str]]] = field(default_factory=dict)  # token -> list of name word lists
    role_counts: dict[str, int] = field(default_factory=dict)
    derived: DerivedFacts = field(default_factory=DerivedFacts)

    def mark(self, index: int, kind: str, detector: str, score: float, token: str | None = None) -> None:
        token = token or TOKEN_BY_KIND.get(kind, "[REDACTED]")
        existing = self.hits.get(index)
        if existing is None:
            self.hits[index] = _Hit(kind, detector, score, token)
            return
        # A specific pseudonym such as [TAXPAYER] beats a generic one such as [ADDRESS]; otherwise keep the
        # higher-scoring hit. Detector names accumulate for the redaction log.
        detectors = "+".join(sorted(set(existing.detector.split("+")) | {detector}))
        new_generic = token in GENERIC_TOKENS
        old_generic = existing.token in GENERIC_TOKENS
        if old_generic and not new_generic or (new_generic == old_generic and score > existing.score):
            self.hits[index] = _Hit(kind, detectors, max(score, existing.score), token)
        else:
            self.hits[index] = _Hit(existing.kind, detectors, max(score, existing.score), existing.token)

    def person_token(self, role: str, name_words: list[str]) -> str:
        key = " ".join(normalize(w) for w in name_words)
        for token, variants in self.persons.items():
            for variant in variants:
                if " ".join(normalize(w) for w in variant) == key:
                    return token
        if role in {"TAXPAYER", "SPOUSE", "PREPARER", "FIRM"}:
            token = f"[{role}]"
        else:
            self.role_counts[role] = self.role_counts.get(role, 0) + 1
            token = f"[{role}_{self.role_counts[role]}]"
        self.persons.setdefault(token, []).append(name_words)
        return token


def detect_pii(words: Sequence[PdfWord], tax_year: int, spacy_model: str | None) -> tuple[PiiDetectionResult, dict[str, str]]:
    """Returns the detection result and the pseudonymization map (token -> real value)."""
    state = _State(words=words, lines=group_lines(words), tax_year=tax_year)
    _detect_anchors(state)
    _detect_dependents(state)
    _detect_signatures(state)
    _detect_patterns(state)
    if spacy_model:
        _detect_presidio(state, spacy_model)
    _propagate_names(state)
    regions = _build_regions(state)
    pii_map = {token: " / ".join(" ".join(v) for v in variants) for token, variants in state.persons.items()}
    result = PiiDetectionResult(
        regions=regions,
        word_tokens={i: hit.token for i, hit in sorted(state.hits.items())},
        derived=state.derived,
    )
    return result, pii_map


def _anchor_matches(line: Line) -> list[tuple[Anchor, int, int]]:
    """Non-overlapping label matches; longer labels win ("Employer's name, address, and ZIP code" over "ZIP code")."""
    candidates = [(anchor, m.start(), m.end()) for anchor in ANCHORS for m in anchor.pattern.finditer(line.text)]
    candidates.sort(key=lambda item: item[2] - item[1], reverse=True)
    chosen: list[tuple[Anchor, int, int]] = []
    for anchor, start, end in candidates:
        if any(start < e and end > s for _, s, e in chosen):
            continue
        if anchor.kind == "dob" and TABLE_HEADER.search(line.text):
            continue  # the dependents table header has its own "Date of birth" column
        chosen.append((anchor, start, end))
    chosen.sort(key=lambda item: item[1])
    return chosen


def _detect_anchors(state: _State) -> None:
    label_positions: dict[int, set[int]] = {}
    per_line: list[list[tuple[Anchor, int, int]]] = []
    for li, line in enumerate(state.lines):
        matches = _anchor_matches(line)
        per_line.append(matches)
        label_positions[li] = {p for _, s, e in matches for p in line.positions_in_span(s, e)}

    for li, line in enumerate(state.lines):
        matches = per_line[li]
        previous_role: str | None = None
        for mi, (anchor, start, end) in enumerate(matches):
            role = anchor.role
            if anchor.inherit_role:
                role = previous_role
                if role is None:
                    continue
            if anchor.kind == "name" and role:
                previous_role = role

            next_start = matches[mi + 1][1] if mi + 1 < len(matches) else None
            label_words = line.positions_in_span(start, end)
            if not label_words:
                continue
            label_x0 = state.words[line.indices[label_words[0]]].reading_box[0]
            label_x1 = state.words[line.indices[label_words[-1]]].reading_box[2]

            # 1) value on the same line, between this label and the next one
            same_line = [
                p
                for p in range(label_words[-1] + 1, len(line.indices))
                if p not in label_positions[li]
                and (next_start is None or line.spans[p][0] < next_start)
                and line.text[line.spans[p][0] : line.spans[p][1]] not in {":", ".", "|"}
            ][: anchor.max_words]
            same_line = [p for p in same_line if not set(line.text[line.spans[p][0] : line.spans[p][1]]) <= {"."}]
            candidates = [line.indices[p] for p in same_line]
            if _is_boilerplate([state.words[i].text for i in candidates]):
                candidates = []

            # 2) otherwise, the value under the label, within the label's column
            if not candidates:
                column_end = (
                    state.words[line.indices[line.word_at_char(next_start) or 0]].reading_box[0] - 2
                    if next_start is not None and line.word_at_char(next_start) is not None
                    else label_x1 + 220
                )
                candidates = _words_below(state, li, label_x0 - 6, column_end, anchor.lines_below, label_positions)
                candidates = candidates[: anchor.max_words * anchor.lines_below]

            # Label and value can run together into one extracted word ("birth10/06/1975").
            overrides: dict[int, str] = {}
            last_span = line.spans[label_words[-1]]
            if last_span[1] > end:
                glued = line.indices[label_words[-1]]
                overrides[glued] = line.text[end : last_span[1]]
                candidates = [glued, *candidates]

            _apply_anchor(state, anchor, role, candidates, overrides)


BOILERPLATE_WORDS = FORM_VOCABULARY | {"have", "p.o", "p.o.", "box", "space", "below", "above", "it", "this"}


def _is_boilerplate(texts: list[str]) -> bool:
    """Instruction text that follows a label on the same line, e.g. "If you have a P.O. box, see instructions."."""
    if not texts:
        return False
    plain = [t for t in texts if normalize(t)]
    if not plain:
        return True
    boring = sum(1 for t in plain if normalize(t) in BOILERPLATE_WORDS or t.islower())
    return boring / len(plain) >= 0.5


def _words_below(
    state: _State, line_index: int, x_start: float, x_end: float, max_lines: int, label_positions: dict[int, set[int]]
) -> list[int]:
    line = state.lines[line_index]
    height = max(line.y1 - line.y0, 6.0)
    found: list[int] = []
    taken_lines = 0
    for lj in range(line_index + 1, len(state.lines)):
        below = state.lines[lj]
        if below.page != line.page:
            break
        if below.y0 - line.y1 > height * 3.5 * (taken_lines + 1):
            break
        def in_range(p: int) -> bool:
            box = state.words[below.indices[p]].reading_box
            return x_start <= (box[0] + box[2]) / 2 <= x_end

        in_column = [below.indices[p] for p in range(len(below.indices)) if p not in label_positions[lj] and in_range(p)]
        has_label_in_column = any(in_range(p) for p in label_positions[lj])
        if in_column:
            found.extend(in_column)
            taken_lines += 1
            if taken_lines >= max_lines:
                break
        elif has_label_in_column:
            break
    return found


def _apply_anchor(
    state: _State, anchor: Anchor, role: str | None, candidates: list[int], overrides: dict[int, str] | None = None
) -> None:
    if not candidates:
        return
    overrides = overrides or {}

    def text_of(i: int) -> str:
        return overrides.get(i, state.words[i].text)

    kind = anchor.kind
    if kind == "name":
        name_words = [i for i in candidates if looks_like_name_word(text_of(i)) or len(normalize(text_of(i))) == 1]
        if not name_words or not any(len(normalize(text_of(i))) > 1 for i in name_words):
            return
        token = state.person_token(role or "PERSON", [text_of(i) for i in name_words if len(normalize(text_of(i))) > 1])
        for i in name_words:
            state.mark(i, "name", "anchor", 0.95, token)
        return
    if kind == "employer":
        # First line is the organization's name, the rest is its address.
        first_line_y = state.words[candidates[0]].reading_box[1]
        name_part = [i for i in candidates if abs(state.words[i].reading_box[1] - first_line_y) < 2]
        rest = [i for i in candidates if i not in name_part]
        if name_part:
            token = state.person_token(role or "EMPLOYER", [text_of(i) for i in name_part])
            for i in name_part:
                state.mark(i, "employer", "anchor", 0.95, token)
        for i in rest:
            state.mark(i, "address", "anchor", 0.9)
        return
    if kind == "dob":
        text = " ".join(text_of(i) for i in candidates)
        born = parse_date(text)
        if not born:
            return
        bracket = age_bracket(born, state.tax_year)
        if role == "SPOUSE":
            state.derived.spouse_age_bracket = bracket
        elif state.derived.taxpayer_age_bracket == "unknown":
            state.derived.taxpayer_age_bracket = bracket
        for i in candidates:
            if DATE_PATTERN.search(text_of(i)) or text_of(i).isdigit():
                state.mark(i, "dob", "anchor", 0.95)
        return
    if kind == "address":
        for i in candidates:
            text = text_of(i).strip(",")
            if text.upper() in US_STATES and len(text) == 2:
                if state.derived.state == "unknown":
                    state.derived.state = text.upper()
                continue
            state.mark(i, "address", "anchor", 0.9)
        return
    for i in candidates:
        if kind in {"bank", "ssn", "ein", "ptin", "ip_pin", "zip"} and not any(c.isdigit() for c in text_of(i)):
            continue
        state.mark(i, kind, "anchor", 0.95)


def _detect_dependents(state: _State) -> None:
    words = state.words
    in_section = False
    row = 0
    for line in state.lines:
        if DEPENDENTS_START.search(line.text):
            in_section = True
            continue
        if not in_section:
            continue
        if DEPENDENTS_END.search(line.text):
            in_section = False
            continue
        if DEPENDENTS_HEADER.search(line.text):
            continue
        texts = [words[i].text for i in line.indices]
        # A dependent row starts with the name; stop at the first word that isn't part of one.
        name_positions: list[int] = []
        for p, text in enumerate(texts):
            if not looks_like_name_word(text) or normalize(text) in RELATIONSHIPS:
                break
            name_positions.append(p)
        if not name_positions:
            continue
        row += 1
        token = state.person_token("DEPENDENT", [texts[p] for p in name_positions])
        for p in name_positions:
            state.mark(line.indices[p], "name", "anchor", 0.9, token)
        born = parse_date(line.text)
        bracket: AgeBracket = age_bracket(born, state.tax_year) if born else "unknown"
        if born:
            for p, text in enumerate(texts):
                if DATE_PATTERN.search(text):
                    state.mark(line.indices[p], "dob", "anchor", 0.95)
        if not any(d.token == token for d in state.derived.dependents):
            state.derived.dependents.append(DerivedDependent(token=token, age_bracket=bracket))


def _detect_signatures(state: _State) -> None:
    words = state.words
    for line in state.lines:
        for m in SIGNATURE_ANCHOR.finditer(line.text):
            positions = line.positions_in_span(m.start(), m.end())
            if not positions:
                continue
            first = words[line.indices[positions[0]]]
            fx0, fy0, _, _ = first.reading_box
            ly1 = words[line.indices[positions[-1]]].reading_box[3]
            height = max(ly1 - fy0, 8)
            # The signature itself is usually a drawing or image with no text, so cover the area by geometry:
            # beside and just below the label, in reading space, mapped back to the page as displayed.
            area = (fx0, fy0 - height * 0.5, fx0 + 260, ly1 + height * 2.5)
            x0, y0, x1, y1 = reading_to_display(area, first.rotation, first.page_width, first.page_height)
            state.extra_boxes.append(
                RedactionRegion(
                    id="", page=line.page, x0=x0, y0=y0, x1=x1, y1=y1, kind="signature", detector="anchor", score=0.9
                )
            )


def _detect_patterns(state: _State) -> None:
    words = state.words
    for i, word in enumerate(words):
        text = word.text.strip("()[],;:")
        if SSN_PATTERN.search(text):
            state.mark(i, "ssn", "regex", 0.95)
        elif EIN_PATTERN.search(text):
            state.mark(i, "ein", "regex", 0.9)
        elif EMAIL_PATTERN.search(text):
            state.mark(i, "email", "regex", 0.95)
        elif PTIN_PATTERN.search(text):
            state.mark(i, "ptin", "regex", 0.9)
        elif ZIP4_PATTERN.search(text):
            state.mark(i, "zip", "regex", 0.85)
        elif LONG_DIGITS_PATTERN.match(text):
            kind = "bank" if len(text) != 9 or aba_checksum_ok(text) else "ssn"
            state.mark(i, kind, "regex", 0.85)

    for line in state.lines:
        for pattern, kind in ((PHONE_PATTERN, "phone"), (SSN_SPACED_PATTERN, "ssn"), (EMAIL_PATTERN, "email")):
            for m in pattern.finditer(line.text):
                for p in line.positions_in_span(m.start(), m.end()):
                    state.mark(line.indices[p], kind, "regex", 0.9)
        for m in CITY_STATE_ZIP_PATTERN.finditer(line.text):
            if m.group(2) not in US_STATES:
                continue
            if state.derived.state == "unknown":
                state.derived.state = m.group(2)
            for p in line.positions_in_span(m.start(1), m.end(1)):
                state.mark(line.indices[p], "address", "regex", 0.85)
            for p in line.positions_in_span(m.start(3), m.end(3)):
                state.mark(line.indices[p], "zip", "regex", 0.85)


def _detect_presidio(state: _State, spacy_model: str) -> None:
    analyzer = get_analyzer(spacy_model)
    words = state.words
    for page in sorted({line.page for line in state.lines}):
        page_lines = [line for line in state.lines if line.page == page]
        text_parts: list[str] = []
        offsets: list[int] = []
        cursor = 0
        for line in page_lines:
            offsets.append(cursor)
            text_parts.append(line.text)
            cursor += len(line.text) + 1
        text = "\n".join(text_parts)
        if not text.strip():
            continue
        results = analyzer.analyze(text=text, language="en", entities=PRESIDIO_ENTITIES)  # type: ignore[attr-defined]
        for result in results:
            kind = PRESIDIO_KIND.get(result.entity_type)
            if not kind:
                continue
            threshold = 0.85 if kind == "name" else 0.4
            if result.score < threshold:
                continue
            span_text = text[result.start : result.end]
            if kind == "location" and (
                span_text.strip().lower() in LOCATION_ALLOWLIST or span_text.strip().upper() in US_STATES
            ):
                continue
            for li, line in enumerate(page_lines):
                start = max(result.start - offsets[li], 0)
                end = min(result.end - offsets[li], len(line.text))
                if end <= 0 or start >= len(line.text):
                    continue
                positions = line.positions_in_span(start, end)
                indices = [line.indices[p] for p in positions]
                if kind == "name":
                    indices = [i for i in indices if looks_like_name_word(words[i].text)]
                    if not indices:
                        continue
                    token = state.hits[indices[0]].token if indices[0] in state.hits else None
                    if token is None or token in TOKEN_BY_KIND.values():
                        token = state.person_token("PERSON", [words[i].text for i in indices])
                    for i in indices:
                        state.mark(i, "name", "presidio", result.score, token)
                else:
                    indices = [i for i in indices if not is_money(words[i].text) or kind in {"bank", "ssn"}]
                    for i in indices:
                        state.mark(i, kind, "presidio", result.score)


def _propagate_names(state: _State) -> None:
    """Redact every other occurrence of a found name, and of the last name alone (§5.5 step 4)."""
    lookup: dict[str, str] = {}
    for token, variants in state.persons.items():
        for variant in variants:
            parts = [normalize(w) for w in variant if len(normalize(w)) > 1 and normalize(w) not in FORM_VOCABULARY]
            for part in parts:
                lookup.setdefault(part, token)
    if not lookup:
        return
    for i, word in enumerate(state.words):
        key = normalize(word.text)
        if key in lookup and i not in state.hits and word.text[:1].isupper():
            state.mark(i, "name", "propagation", 0.9, lookup[key])


def _build_regions(state: _State) -> list[RedactionRegion]:
    boxes: list[RedactionRegion] = []
    for i, hit in state.hits.items():
        w = state.words[i]
        boxes.append(
            RedactionRegion(
                id="",
                page=w.page,
                x0=w.x0 - PAD,
                y0=w.y0 - PAD,
                x1=w.x1 + PAD,
                y1=w.y1 + PAD,
                kind=hit.kind,
                detector=hit.detector,
                score=round(hit.score, 3),
            )
        )
    boxes.extend(state.extra_boxes)
    merged = merge_regions(boxes)
    return [r.model_copy(update={"id": f"r{n}"}) for n, r in enumerate(merged, start=1)]


def merge_regions(regions: Iterable[RedactionRegion]) -> list[RedactionRegion]:
    """Merge overlapping boxes of the same kind on the same page (§5.5 step 5)."""
    pending = sorted(regions, key=lambda r: (r.page, r.y0, r.x0))
    merged: list[RedactionRegion] = []
    for region in pending:
        for k, existing in enumerate(merged):
            if (
                existing.page == region.page
                and existing.kind == region.kind
                and region.x0 <= existing.x1
                and region.x1 >= existing.x0
                and region.y0 <= existing.y1
                and region.y1 >= existing.y0
            ):
                detectors = sorted(set(existing.detector.split("+")) | set(region.detector.split("+")))
                merged[k] = existing.model_copy(
                    update={
                        "x0": min(existing.x0, region.x0),
                        "y0": min(existing.y0, region.y0),
                        "x1": max(existing.x1, region.x1),
                        "y1": max(existing.y1, region.y1),
                        "detector": "+".join(detectors),
                        "score": max(existing.score, region.score),
                    }
                )
                break
        else:
            merged.append(region)
    return merged


def words_in_regions(words: Sequence[PdfWord], regions: Sequence[RedactionRegion], min_overlap: float = 0.3) -> set[int]:
    """Indices of words at least `min_overlap` covered by any region."""
    covered: set[int] = set()
    for i, w in enumerate(words):
        area = max((w.x1 - w.x0) * (w.y1 - w.y0), 1e-6)
        for r in regions:
            if r.page != w.page:
                continue
            ix = min(w.x1, r.x1) - max(w.x0, r.x0)
            iy = min(w.y1, r.y1) - max(w.y0, r.y0)
            if ix > 0 and iy > 0 and ix * iy / area >= min_overlap:
                covered.add(i)
                break
    return covered


def scrub_text(value: str, known_values: Iterable[str] = ()) -> str:
    """Remove PII-shaped strings (and any known real values) from LLM output before storing it (§6.6)."""
    for known in sorted({v for v in known_values if len(v) > 1}, key=len, reverse=True):
        value = re.sub(rf"\b{re.escape(known)}\b", "[REDACTED]", value, flags=re.IGNORECASE)
    value = SSN_PATTERN.sub("[SSN]", value)
    value = EIN_PATTERN.sub("[EIN]", value)
    value = EMAIL_PATTERN.sub("[EMAIL]", value)
    value = PHONE_PATTERN.sub("[PHONE]", value)
    value = PTIN_PATTERN.sub("[PTIN]", value)
    value = re.sub(r"\b\d{9,17}\b", "[NUMBER]", value)
    return value
