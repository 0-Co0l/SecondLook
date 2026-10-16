"""Map redacted words to structured tax fields (§6.1).

Input words have already been masked: anything under a confirmed redaction region carries its pseudonym
token (e.g. "[TAXPAYER]") instead of its text, so nothing built here can contain redacted PII.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .layout import Line, group_lines, normalize
from .models import (
    DependentProfile,
    DerivedFacts,
    PersonProfile,
    Questionnaire,
    RedactionRegion,
    TaxReviewInput,
)
from .pdf import PdfWord
from .pii import MONEY_PATTERN, RELATIONSHIPS, words_in_regions


@dataclass(frozen=True)
class FieldSpec:
    path: str  # dotted path in TaxReviewInput
    label: re.Pattern[str]
    row_context: re.Pattern[str] | None = None  # only match when the row also contains this


def _f(path: str, label: str, row_context: str | None = None) -> FieldSpec:
    return FieldSpec(
        path, re.compile(label, re.IGNORECASE), re.compile(row_context, re.IGNORECASE) if row_context else None
    )


# Earlier specs for the same path win (e.g. 1040 line 1z before line 1a).
FIELD_SPECS: tuple[FieldSpec, ...] = (
    _f("income.wages", r"add lines 1a through 1h"),
    _f("income.wages", r"total amount from form\(s\) w-2"),
    _f("income.tax_exempt_interest", r"tax-exempt interest"),
    _f("income.taxable_interest", r"\btaxable interest"),
    _f("income.qualified_dividends", r"qualified dividends"),
    _f("income.ordinary_dividends", r"ordinary dividends"),
    _f("income.ira_distributions", r"ira distributions"),
    _f("income.ira_distributions_taxable", r"taxable amount", r"ira distributions"),
    _f("income.pensions", r"pensions and annuities"),
    _f("income.pensions_taxable", r"taxable amount", r"pensions and annuities"),
    _f("income.social_security", r"social security benefits"),
    _f("income.social_security_taxable", r"taxable amount", r"social security benefits"),
    _f("income.capital_gain", r"capital gain or \(loss\)"),
    _f("income.schedule_c_net", r"business income or \(loss\)"),
    _f("income.schedule_c_net", r"net profit or \(loss\)"),
    _f("income.schedule_e_net", r"rental real estate, royalties, partnerships"),
    _f("income.unemployment", r"unemployment compensation"),
    _f("income.total_income", r"this is your total income"),
    _f("income.agi", r"this is your adjusted gross income"),
    _f("adjustments.educator_expenses", r"educator expenses"),
    _f("adjustments.hsa_deduction", r"health savings account deduction"),
    _f("adjustments.se_tax_deduction", r"deductible part of self-employment tax"),
    _f("adjustments.se_retirement", r"self-employed sep, simple,? and qualified plans"),
    _f("adjustments.se_health_insurance", r"self-employed health insurance deduction"),
    _f("adjustments.ira_deduction", r"\bira deduction"),
    _f("adjustments.student_loan_interest", r"student loan interest deduction"),
    _f("adjustments.total_adjustments", r"adjustments to income from schedule 1"),
    _f("deduction.amount", r"standard deduction or itemized deductions"),
    _f("deduction.qbi_deduction", r"qualified business income deduction"),
    _f("deduction.taxable_income", r"this is your taxable income"),
    _f("credits_claimed.child_tax_credit", r"child tax credit or credit for other dependents"),
    _f("credits_claimed.additional_child_tax_credit", r"additional child tax credit"),
    _f("credits_claimed.child_dependent_care", r"child and dependent care expenses"),
    _f("credits_claimed.education_refundable", r"american opportunity credit from form 8863"),
    _f("credits_claimed.education", r"education credits from form 8863"),
    _f("credits_claimed.retirement_savers", r"retirement savings contributions? credit"),
    _f("credits_claimed.residential_energy", r"residential (?:clean )?energy credit"),
    _f("credits_claimed.residential_energy", r"energy efficient home improvement credit"),
    _f("credits_claimed.clean_vehicle", r"clean vehicle credit"),
    _f("credits_claimed.earned_income_credit", r"earned income credit"),
    _f("credits_claimed.premium_tax_credit", r"net premium tax credit"),
    _f("payments.federal_withholding", r"add lines 25a through 25c"),
    _f("payments.federal_withholding", r"federal income tax withheld from"),
    _f("payments.estimated_payments", r"estimated tax payments"),
    _f("payments.total_payments", r"these are your total payments"),
    _f("tax.total_tax", r"this is your total tax"),
    _f("tax.refund", r"this is the amount you overpaid"),
    _f("tax.amount_owed", r"amount you owe"),
)

LINE_REF = re.compile(r"^\s*(\d{1,2}[a-z]?)\s")
ALL_LABELS = re.compile("|".join(f"(?:{spec.label.pattern})" for spec in FIELD_SPECS), re.IGNORECASE)

FILING_STATUS_OPTIONS = (
    ("married_filing_jointly", re.compile(r"married filing jointly", re.IGNORECASE)),
    ("married_filing_separately", re.compile(r"married filing separately", re.IGNORECASE)),
    ("head_of_household", re.compile(r"head of household", re.IGNORECASE)),
    ("qualifying_surviving_spouse", re.compile(r"qualifying surviving spouse", re.IGNORECASE)),
    ("single", re.compile(r"\bsingle\b", re.IGNORECASE)),
)
CHECK_MARKS = {"x", "☒", "☑", "✓", "✔", "■", "[x]", "(x)", "✗", "✘"}

FORM_HEADER = re.compile(
    r"\b(?:(SCHEDULE)\s+(1|2|3|A|B|C|D|E|SE|8812)|Form\s+(1040|8863|8880|2441|5695|8962|8995|W-2))\b"
)
SCHEDULE_A = re.compile(r"\bitemized deductions\b", re.IGNORECASE)


def mask_words(
    words: Sequence[PdfWord], regions: Sequence[RedactionRegion], word_tokens: dict[int, str]
) -> list[PdfWord]:
    """Replace every word under a confirmed region with its pseudonym token (or [REDACTED])."""
    covered = words_in_regions(words, regions)
    return [replace(w, text=word_tokens.get(i, "[REDACTED]")) if i in covered else w for i, w in enumerate(words)]


def parse_money(text: str) -> float | None:
    text = text.strip()
    if not MONEY_PATTERN.match(text) and not re.fullmatch(r"-?\d+(?:\.\d{2})?\.?", text):
        return None
    negative = text.startswith("(") or text.startswith("-")
    digits = re.sub(r"[^\d.]", "", text).rstrip(".")
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return -value if negative else value


def build_review_input(
    words: Sequence[PdfWord],
    tax_year: int,
    questionnaire: dict[str, str],
    derived: DerivedFacts,
    unreadable_pages: Sequence[int] = (),
) -> TaxReviewInput:
    lines = group_lines(words)
    values: dict[str, float] = {}
    unreadable: list[str] = [f"page {p + 1} (no readable text)" for p in unreadable_pages]

    for line in lines:
        _extract_line_fields(line, words, values, unreadable)

    payload = TaxReviewInput(
        tax_year=tax_year,
        filing_status=_filing_status(lines, words),
        state=derived.state,
        user_context=Questionnaire.model_validate(questionnaire),
        forms_present=_forms_present(lines, words),
    )
    for path, value in values.items():
        section, name = path.split(".")
        setattr(getattr(payload, section), name, value)

    payload.taxpayer = PersonProfile(age_bracket=derived.taxpayer_age_bracket)
    born_before, blind = _age_blind_boxes(lines, words)
    if born_before.get("you"):
        payload.taxpayer.age_bracket = "65+"
    payload.taxpayer.blind = blind.get("you")
    if payload.filing_status in {"married_filing_jointly", "married_filing_separately"}:
        payload.spouse = PersonProfile(age_bracket=derived.spouse_age_bracket, blind=blind.get("spouse"))
        if born_before.get("spouse"):
            payload.spouse.age_bracket = "65+"

    payload.dependents = _dependents(lines, words, derived)

    if "Schedule A" in payload.forms_present:
        payload.deduction.type = "itemized"
    elif payload.deduction.amount is not None:
        payload.deduction.type = "standard"
    payload.unreadable_fields = unreadable
    return payload


def _extract_line_fields(line: Line, words: Sequence[PdfWord], values: dict[str, float], unreadable: list[str]) -> None:
    label_matches = sorted(((m.start(), m.end()) for m in ALL_LABELS.finditer(line.text)), key=lambda m: m[0])
    if not label_matches:
        return
    leading = LINE_REF.match(line.text)
    line_refs = {leading.group(1).lower()} if leading else set()
    for spec in FIELD_SPECS:
        if spec.path in values:
            continue
        if spec.row_context and not spec.row_context.search(line.text):
            continue
        for m in spec.label.finditer(line.text):
            following = [s for s, _ in label_matches if s >= m.end()]
            segment_end = following[0] if following else len(line.text)
            positions = line.positions_in_span(m.end(), segment_end)
            texts = [words[line.indices[p]].text for p in positions]
            # The line number is repeated next to the amount box ("12 Standard deduction ... 12 29,200");
            # never mistake it for the amount when the box is blank.
            texts = [t for t in texts if t.strip().lower() not in line_refs]
            amounts = [parse_money(t) for t in texts]
            amounts = [a for a in amounts if a is not None]
            if amounts:
                # The last number in the segment is the amount column; earlier ones are line references.
                values[spec.path] = amounts[-1]
                break
            if any(t.startswith("[") for t in texts) or any(re.search(r"\d", t) for t in texts if len(t) > 2):
                if spec.path not in unreadable:
                    unreadable.append(spec.path)
                break


def _checked_before(line: Line, words: Sequence[PdfWord], char_offset: int) -> bool:
    position = line.word_at_char(char_offset)
    if position is None or position == 0:
        return False
    return normalize(words[line.indices[position - 1]].text) in CHECK_MARKS or (
        words[line.indices[position - 1]].text.strip().lower() in CHECK_MARKS
    )


def _filing_status(lines: list[Line], words: Sequence[PdfWord]) -> str:
    for line in lines:
        for status, pattern in FILING_STATUS_OPTIONS:
            for m in pattern.finditer(line.text):
                if _checked_before(line, words, m.start()):
                    return status
    return "unknown"


def _age_blind_boxes(lines: list[Line], words: Sequence[PdfWord]) -> tuple[dict[str, bool], dict[str, bool]]:
    born_before: dict[str, bool] = {}
    blind: dict[str, bool] = {}
    for line in lines:
        lowered = line.text.lower()
        if "born before" not in lowered and "blind" not in lowered:
            continue
        spouse_at = lowered.find("spouse:")
        for m in re.finditer(r"(?:were|was) born before", lowered):
            who = "spouse" if 0 <= spouse_at < m.start() else "you"
            born_before[who] = _checked_before(line, words, m.start())
        for m in re.finditer(r"(?:are|is) blind", lowered):
            who = "spouse" if 0 <= spouse_at < m.start() else "you"
            blind[who] = _checked_before(line, words, m.start())
    return born_before, blind


def _forms_present(lines: list[Line], words: Sequence[PdfWord]) -> list[str]:
    found: list[str] = []
    seen_pages: dict[int, int] = {}
    for line in lines:
        seen_pages[line.page] = seen_pages.get(line.page, 0) + 1
        if seen_pages[line.page] > 4:  # form titles sit at the top of the page
            continue
        for m in FORM_HEADER.finditer(line.text):
            name = f"Schedule {m.group(2)}" if m.group(1) else f"Form {m.group(3)}" if m.group(3) != "1040" else "1040"
            if name == "Form W-2":
                name = "W-2"
            if name not in found:
                found.append(name)
    return found


def _dependents(lines: list[Line], words: Sequence[PdfWord], derived: DerivedFacts) -> list[DependentProfile]:
    profiles: dict[str, DependentProfile] = {}
    header: Line | None = None
    for line in lines:
        lowered = line.text.lower()
        if header is None and "tax credit" in lowered and "relationship" in lowered:
            header = line
        tokens = [words[i].text for i in line.indices]
        dependent_tokens = [t for t in tokens if t.startswith("[DEPENDENT_")]
        if not dependent_tokens:
            continue
        token = dependent_tokens[0]
        if token in profiles:
            continue
        relationship = next((normalize(t) for t in tokens if normalize(t) in RELATIONSHIPS), "unknown")
        profile = DependentProfile(id=token.strip("[]"), relationship=relationship)
        if header is not None and header.page == line.page:
            ctc_x, odc_x = _column_centers(header, words)
            for i in line.indices:
                if words[i].text.strip().lower() in CHECK_MARKS:
                    box = words[i].reading_box
                    center = (box[0] + box[2]) / 2
                    if ctc_x is not None and (odc_x is None or abs(center - ctc_x) < abs(center - odc_x)):
                        profile.child_tax_credit_checked = True
                    elif odc_x is not None:
                        profile.other_dependent_credit_checked = True
        profiles[token] = profile

    for dependent in derived.dependents:
        key = dependent.token
        if key in profiles:
            profiles[key].age_bracket = dependent.age_bracket
        else:
            profiles[key] = DependentProfile(id=key.strip("[]"), age_bracket=dependent.age_bracket)
    return list(profiles.values())


def _column_centers(header: Line, words: Sequence[PdfWord]) -> tuple[float | None, float | None]:
    lowered = header.text.lower()

    def center_of(phrase: str) -> float | None:
        at = lowered.find(phrase)
        if at < 0:
            return None
        positions = header.positions_in_span(at, at + len(phrase))
        xs = [words[header.indices[p]] for p in positions]
        return (xs[0].reading_box[0] + xs[-1].reading_box[2]) / 2 if xs else None

    return center_of("child tax credit") or center_of("tax credit"), center_of("other dependent")
