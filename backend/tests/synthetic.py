"""Synthetic tax returns for tests. Every person, number and address here is invented (§11: never real data).

Each return records the PII values seeded into it (which must never survive redaction) and the values the
field mapper is expected to read back.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

import pymupdf

VARIANTS = ("text", "fonts", "rotated", "scanned", "scanned_rotated", "form")

FIRST_NAMES = [
    "Marisol", "Dorian", "Ingrid", "Tobias", "Juniper", "Caspian", "Odessa", "Leopold", "Rosalind", "Thaddeus",
    "Wren", "Ezekiel", "Clementine", "Fitzgerald", "Saoirse", "Anselm",
]
LAST_NAMES = [
    "Quillfeather", "Abernathy", "Montclair", "Okonkwo", "Haverford", "Lindqvist", "Castellanos", "Pemberton",
    "Nakamura", "Vasquez-Orr", "Delacroix", "Brannigan",
]
CITIES = ["Briarwood", "Willowmere", "Fernhaven", "Ashcombe", "Cedarbrook"]
STREETS = ["Larkspur Lane", "Juniper Court", "Oldmill Road", "Tamarack Street", "Heron Ridge Drive"]
EMPLOYERS = ["Zephyrline Logistics", "Brightwater Analytics", "Copperleaf Foods", "Halcyon Robotics"]
FIRMS = ["Ledgerwise Tax Partners", "Northgate Accounting"]
STATES = ["IL", "OR", "GA", "MN", "AZ"]


def _ssn(rng: random.Random) -> str:
    return f"{rng.randint(100, 665)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"


def _ein(rng: random.Random) -> str:
    return f"{rng.randint(10, 98)}-{rng.randint(1000000, 9999999)}"


def _routing(rng: random.Random) -> str:
    while True:
        digits = [rng.randint(0, 9) for _ in range(8)]
        digits[0] = rng.randint(0, 2)
        for check in range(10):
            d = digits + [check]
            if (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0:
                return "".join(map(str, d))


def _money(value: float) -> str:
    return f"{value:,.0f}"


@dataclass
class SyntheticReturn:
    variant: str
    seed: int
    pdf: bytes
    pii: list[str]
    expected: dict[str, Any] = field(default_factory=dict)


class _Writer:
    def __init__(self, doc: pymupdf.Document, variant: str, rng: random.Random) -> None:
        self.doc = doc
        self.variant = variant
        self.rng = rng
        self.page: pymupdf.Page | None = None
        self.fontname = {"fonts": "cour"}.get(variant, "helv")
        self.value_font = {"fonts": "tiro"}.get(variant, "helv")
        self.widget_count = 0

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=612, height=792)

    def label(self, x: float, y: float, text: str, size: float = 7.5) -> None:
        assert self.page is not None
        self.page.insert_text((x, y), text, fontsize=size, fontname=self.fontname)

    def value(self, x: float, y: float, text: str, size: float = 10, width: float = 150) -> None:
        """Values the taxpayer typed. In the "form" variant they are fillable form fields instead of text."""
        assert self.page is not None
        if self.variant == "form":
            widget = pymupdf.Widget()
            widget.field_name = f"f{self.widget_count}"
            self.widget_count += 1
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.rect = pymupdf.Rect(x, y - size, x + width, y + 3)
            widget.field_value = text
            widget.text_fontsize = size
            self.page.add_widget(widget)
        else:
            self.page.insert_text((x, y), text, fontsize=size, fontname=self.value_font)

    def amount_line(self, y: float, label: str, ref: str, amount: float | None, x_label: float = 40) -> None:
        self.label(x_label, y, label)
        self.label(458, y, ref)
        if amount is not None:
            text = _money(amount)
            self.value(560 - len(text) * 5.2, y, text, size=9, width=60)


def make_return(variant: str = "text", seed: int = 1, tax_year: int = 2025) -> SyntheticReturn:
    rng = random.Random(f"{variant}-{seed}")
    upper = variant == "fonts"

    def case(text: str) -> str:
        return text.upper() if upper else text

    married = rng.random() < 0.6
    taxpayer_first, spouse_first, *dep_firsts = rng.sample(FIRST_NAMES, 5)
    last = rng.choice(LAST_NAMES)
    dependents = dep_firsts[: rng.randint(1, 2)]
    city, street, state = rng.choice(CITIES), rng.choice(STREETS), rng.choice(STATES)
    house = str(rng.randint(100, 9899))
    apt = f"{rng.randint(41, 99)}{rng.choice('ABCD')}"  # clear of 1040 line numbers such as "5b"
    zip_code = f"{rng.randint(10000, 99899)}"
    phone = f"({rng.randint(201, 989)}) {rng.randint(200, 989)}-{rng.randint(1000, 9999)}"
    email = f"{taxpayer_first.lower()}.{last.lower()}@example.net"
    ssns = [_ssn(rng) for _ in range(2 + len(dependents))]
    routing, account = _routing(rng), str(rng.randint(10**9, 10**11))
    taxpayer_dob = f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/{rng.randint(1965, 1990)}"
    dep_dobs = [f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/{rng.randint(2012, 2020)}" for _ in dependents]
    preparer_first, preparer_last = rng.choice(FIRST_NAMES[10:]), rng.choice(LAST_NAMES[8:])
    ptin = f"P{rng.randint(10**7, 10**8 - 1)}"
    firm, firm_ein = rng.choice(FIRMS), _ein(rng)
    employer, employer_ein = rng.choice(EMPLOYERS), _ein(rng)
    ip_pin = str(rng.randint(100000, 999999))

    wages = rng.randint(42, 160) * 1000 + rng.randint(0, 999)
    taxable_interest = rng.randint(50, 1500)
    business = rng.randint(2, 15) * 1000
    se_deduction = round(business * 0.0706)
    total_income = wages + taxable_interest + business
    agi = total_income - se_deduction
    standard = 31500 if married else 23625
    taxable = max(agi - standard, 0)
    withholding = round(wages * 0.11)
    ctc = 2200 * len(dependents)
    status = "married_filing_jointly" if married else "head_of_household"

    doc = pymupdf.open()
    w = _Writer(doc, variant, rng)

    # ---- Form 1040, page 1 ----
    w.new_page()
    w.label(40, 36, "Form 1040   Department of the Treasury - Internal Revenue Service", 9)
    w.label(40, 48, f"U.S. Individual Income Tax Return {tax_year}   OMB No. 1545-0074", 9)
    w.label(40, 64, "Filing Status   Check only one box.")
    options = [
        ("single", "Single"),
        ("married_filing_jointly", "Married filing jointly"),
        ("married_filing_separately", "Married filing separately (MFS)"),
        ("head_of_household", "Head of household (HOH)"),
    ]
    x = 40
    for key, text in options:
        if key == status:
            w.value(x, 76, "X", size=8, width=8)
        w.label(x + 10, 76, text)
        x += 10 + pymupdf.get_text_length(text, fontname=w.fontname, fontsize=7.5) + 14
    w.label(40, 96, "Your first name and middle initial")
    w.label(230, 96, "Last name")
    w.label(430, 96, "Your social security number")
    w.value(40, 110, case(f"{taxpayer_first} {rng.choice('ABCDEFG')}"))
    w.value(230, 110, case(last))
    w.value(430, 110, ssns[0])
    w.label(40, 128, "If joint return, spouse's first name and middle initial")
    w.label(230, 128, "Last name")
    w.label(430, 128, "Spouse's social security number")
    if married:
        w.value(40, 142, case(spouse_first))
        w.value(230, 142, case(last))
        w.value(430, 142, ssns[1])
    w.label(40, 160, "Home address (number and street). If you have a P.O. box, see instructions.")
    w.label(400, 160, "Apt. no.")
    w.value(40, 174, case(f"{house} {street}"), width=250)
    w.value(400, 174, apt, width=40)
    w.label(40, 192, "City, town, or post office. If you have a foreign address, also complete spaces below.")
    w.label(380, 192, "State")
    w.label(430, 192, "ZIP code")
    w.value(40, 206, case(city), width=200)
    w.value(380, 206, state, width=30)
    w.value(430, 206, zip_code, width=60)
    w.label(40, 224, "Phone no.")
    w.value(90, 224, phone, size=9, width=110)
    w.label(260, 224, "Email address")
    w.value(320, 224, email, size=9, width=200)
    w.label(40, 240, "Your date of birth")
    w.value(120, 240, taxpayer_dob, size=9, width=70)
    w.label(40, 256, "Age/Blindness   You: Were born before January 2, 1961   Are blind   Spouse: Was born before January 2, 1961   Is blind")

    w.label(40, 276, "Dependents (see instructions):", 8)
    w.label(40, 290, "(1) First name   Last name")
    w.label(200, 290, "(2) Social security number")
    w.label(320, 290, "(3) Relationship", 6.5)
    w.label(392, 290, "Date of birth", 6.5)
    w.label(452, 290, "Child tax credit", 6.5)
    w.label(530, 290, "Other dependents", 6.5)
    relationships = ["daughter", "son"]
    for n, first in enumerate(dependents):
        y = 304 + n * 14
        w.value(40, y, case(f"{first} {last}"), size=9)
        w.value(200, y, ssns[2 + n], size=9, width=90)
        w.value(320, y, relationships[n % 2], size=9, width=60)
        w.value(400, y, dep_dobs[n], size=9, width=60)
        w.value(475, y, "X", size=8, width=8)

    y = 350
    lines = [
        ("1a Total amount from Form(s) W-2, box 1 (see instructions) . . . . . .", "1a", wages),
        ("1z Add lines 1a through 1h . . . . . . . . . . . . . .", "1z", wages),
        ("7 Capital gain or (loss). Attach Schedule D if required . . . . .", "7", None),
        ("8 Additional income from Schedule 1, line 10 . . . . . . . .", "8", business),
        ("9 Add lines 1z, 2b, 3b, 4b, 5b, 6b, 7, and 8. This is your total income . .", "9", total_income),
        ("10 Adjustments to income from Schedule 1, line 26 . . . . . .", "10", se_deduction),
        ("11 Subtract line 10 from line 9. This is your adjusted gross income . .", "11", agi),
        ("12 Standard deduction or itemized deductions (from Schedule A) . . .", "12", standard),
        ("15 Subtract line 14 from line 11. This is your taxable income . . .", "15", taxable),
        ("19 Child tax credit or credit for other dependents from Schedule 8812 . .", "19", ctc),
        ("24 Add lines 22 and 23. This is your total tax . . . . . . .", "24", round(taxable * 0.12)),
        ("25d Add lines 25a through 25c . . . . . . . . . . . . .", "25d", withholding),
    ]
    for label, ref, amount in lines[:2]:
        w.amount_line(y, label, ref, amount)
        y += 14
    w.label(40, y, "2a Tax-exempt interest . . 2a")
    w.label(250, y, "b Taxable interest . . . . . . . . . . .")
    w.label(458, y, "2b")
    w.value(560 - len(_money(taxable_interest)) * 5.2, y, _money(taxable_interest), size=9, width=60)
    y += 14
    for label, ref, amount in lines[2:]:
        w.amount_line(y, label, ref, amount)
        y += 14
    w.label(40, y + 6, "Identity Protection PIN")
    w.value(150, y + 6, ip_pin, size=9, width=60)

    # ---- Form 1040, page 2 ----
    w.new_page()
    w.label(40, 36, "Form 1040 (2025)   Page 2", 9)
    refund = max(withholding - round(taxable * 0.12) + ctc, 0)
    w.amount_line(60, "34 If line 33 is more than line 24, this is the amount you overpaid . . .", "34", refund)
    w.label(40, 80, "Direct deposit?  Routing number")
    w.value(170, 80, routing, size=9, width=90)
    w.label(280, 80, "Type: Checking")
    w.label(360, 80, "Account number")
    w.value(430, 80, account, size=9, width=120)
    w.label(40, 110, "Sign Here   Under penalties of perjury, I declare that I have examined this return.")
    w.label(40, 126, "Your signature")
    w.value(110, 126, case(f"{taxpayer_first} {last}"), size=11)
    w.label(330, 126, "Date")
    w.value(360, 126, "03/14/2026", size=9, width=70)
    w.label(40, 150, "Spouse's signature. If a joint return, both must sign.")
    w.label(40, 180, "Paid Preparer Use Only")
    w.label(40, 194, "Preparer's name")
    w.value(110, 194, case(f"{preparer_first} {preparer_last}"), size=9)
    w.label(330, 194, "PTIN")
    w.value(360, 194, ptin, size=9, width=80)
    w.label(40, 208, "Firm's name")
    w.value(110, 208, case(firm), size=9, width=200)
    w.label(330, 208, "Firm's EIN")
    w.value(380, 208, firm_ein, size=9, width=80)

    # ---- Schedule 1 ----
    w.new_page()
    w.label(40, 36, "SCHEDULE 1 (Form 1040)   Additional Income and Adjustments to Income", 9)
    w.label(40, 50, f"Name(s) shown on Form 1040 or 1040-SR")
    w.value(220, 50, case(f"{taxpayer_first} {last}"), size=9)
    w.label(430, 50, "Your social security number")
    w.value(430, 62, ssns[0], size=9, width=90)
    w.amount_line(84, "3 Business income or (loss). Attach Schedule C . . . . . . . .", "3", business)
    w.amount_line(98, "10 Combine lines 1 through 7 and 9. This is your additional income . .", "10", business)
    w.amount_line(112, "15 Deductible part of self-employment tax. Attach Schedule SE . . . .", "15", se_deduction)
    w.amount_line(126, "21 Student loan interest deduction . . . . . . . . . . .", "21", None)

    # ---- W-2 ----
    w.new_page()
    w.label(40, 36, "Form W-2   Wage and Tax Statement   2025", 9)
    w.label(40, 56, "a Employee's social security number")
    w.value(40, 70, ssns[0], size=9, width=90)
    w.label(300, 56, "1 Wages, tips, other compensation")
    w.value(300, 70, f"{wages:.2f}", size=9, width=90)
    w.label(40, 90, "b Employer identification number (EIN)")
    w.value(40, 104, employer_ein, size=9, width=90)
    w.label(40, 124, "c Employer's name, address, and ZIP code")
    w.value(40, 138, case(employer), size=9, width=200)
    w.value(40, 150, case(f"{rng.randint(10, 999)} Commerce Parkway"), size=9, width=200)
    w.value(40, 162, case(f"{city}, {state} {zip_code}"), size=9, width=200)
    w.label(40, 182, "e Employee's first name and initial   Last name")
    w.value(40, 196, case(f"{taxpayer_first} {last}"), size=9)
    w.label(40, 214, "f Employee's address and ZIP code")
    w.value(40, 228, case(f"{house} {street}"), size=9, width=200)

    if variant in {"rotated", "scanned_rotated"}:
        for page in doc:
            page.set_rotation(90)
    doc.set_metadata({"author": f"{taxpayer_first} {last}", "title": f"{last} {tax_year} return"})
    doc.embfile_add("notes.txt", f"{taxpayer_first} {last} SSN {ssns[0]}".encode())
    pdf = doc.tobytes()
    doc.close()

    if variant.startswith("scanned"):
        pdf = _rasterize(pdf)  # a rotated page rasterizes to a sideways scan

    pii = [
        taxpayer_first, last, *dependents, *ssns, f"{house} {street}", street.split()[0], apt, city, zip_code,
        phone, email, taxpayer_dob, *dep_dobs, routing, account, preparer_first, preparer_last, ptin,
        firm.split()[0], firm_ein, employer.split()[0], employer_ein, ip_pin,
    ]
    if married:
        pii.append(spouse_first)
    expected = {
        "filing_status": status,
        "state": state,
        "income.wages": wages,
        "income.taxable_interest": taxable_interest,
        "income.agi": agi,
        "income.schedule_c_net": business,
        "adjustments.se_tax_deduction": se_deduction,
        "deduction.amount": standard,
        "credits_claimed.child_tax_credit": ctc,
        "payments.federal_withholding": withholding,
        "dependents": len(dependents),
    }
    return SyntheticReturn(variant=variant, seed=seed, pdf=pdf, pii=pii, expected=expected)


def _rasterize(pdf: bytes, dpi: int = 200) -> bytes:
    """Simulate a scan: image-only pages, no text layer."""
    source = pymupdf.open(stream=pdf, filetype="pdf")
    scanned = pymupdf.open()
    for page in source:
        pixmap = page.get_pixmap(dpi=dpi, annots=True)
        new_page = scanned.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=pixmap.tobytes("png"))
    out = scanned.tobytes(garbage=4, deflate=True)
    source.close()
    scanned.close()
    return out
