"""SQLModel tables plus every Pydantic schema shared between modules (§10.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Column, DateTime, String
from sqlalchemy.types import JSON, TypeDecorator
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel


AgeBracket = Literal["under 17", "17-23", "24-64", "65+", "unknown"]
Answer = Literal["yes", "no", "unknown"]
FilingStatus = Literal[
    "single",
    "married_filing_jointly",
    "married_filing_separately",
    "head_of_household",
    "qualifying_surviving_spouse",
    "unknown",
]


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Stores UTC and always hands back timezone-aware values (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# ---------------------------------------------------------------------------
# Database tables (§8.1)
# ---------------------------------------------------------------------------


class JobStatus(StrEnum):
    UPLOADED = "uploaded"
    REDACTING = "redacting"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = SQLField(default="local", index=True)
    status: JobStatus = SQLField(
        default=JobStatus.UPLOADED, sa_column=Column(String(32), index=True, nullable=False)
    )
    tax_year: int
    page_count: int = 0
    redaction_log: list[dict[str, Any]] = SQLField(default_factory=list, sa_column=Column(JSON, nullable=False))
    questionnaire: dict[str, Any] = SQLField(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error_code: str | None = None
    created_at: datetime = SQLField(default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False))
    updated_at: datetime = SQLField(default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False))
    expires_at: datetime = SQLField(sa_column=Column(UTCDateTime, index=True, nullable=False))


class Report(SQLModel, table=True):
    __tablename__ = "reports"

    job_id: str = SQLField(primary_key=True, foreign_key="jobs.id")
    model: str
    rules_version: str
    findings: dict[str, Any] = SQLField(default_factory=dict, sa_column=Column(JSON, nullable=False))
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: datetime = SQLField(default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False))


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class Box(BaseModel):
    """A rectangle in PDF points, origin at the top-left of the page as displayed."""

    page: int = Field(ge=0)
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> "Box":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("box must have positive width and height")
        return self


class RedactionRegion(Box):
    """One redaction-log entry. Positions and types only; never the redacted text (§5.5 step 6)."""

    id: str
    kind: str
    detector: str
    score: float = 1.0


class DerivedDependent(BaseModel):
    token: str
    age_bracket: AgeBracket = "unknown"


class DerivedFacts(BaseModel):
    """Tax-relevant attributes kept in place of the PII they came from (§5.1)."""

    taxpayer_age_bracket: AgeBracket = "unknown"
    spouse_age_bracket: AgeBracket = "unknown"
    state: str = "unknown"
    dependents: list[DerivedDependent] = Field(default_factory=list)


class PiiDetectionResult(BaseModel):
    regions: list[RedactionRegion]
    word_tokens: dict[int, str]  # word index -> pseudonym token, e.g. "[TAXPAYER]"
    derived: DerivedFacts


class PatchRedactionsRequest(BaseModel):
    add: list[Box] = Field(default_factory=list)
    remove_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM input (§6.1)
# ---------------------------------------------------------------------------

class PersonProfile(BaseModel):
    age_bracket: AgeBracket = "unknown"
    blind: bool | None = None


class DependentProfile(BaseModel):
    id: str
    relationship: str = "unknown"
    age_bracket: AgeBracket = "unknown"
    months_in_home: int | None = None
    student: bool | None = None
    disabled: bool | None = None
    child_tax_credit_checked: bool | None = None
    other_dependent_credit_checked: bool | None = None


class Income(BaseModel):
    wages: float | None = None
    tax_exempt_interest: float | None = None
    taxable_interest: float | None = None
    qualified_dividends: float | None = None
    ordinary_dividends: float | None = None
    ira_distributions: float | None = None
    ira_distributions_taxable: float | None = None
    pensions: float | None = None
    pensions_taxable: float | None = None
    social_security: float | None = None
    social_security_taxable: float | None = None
    capital_gain: float | None = None
    schedule_c_net: float | None = None
    schedule_e_net: float | None = None
    unemployment: float | None = None
    total_income: float | None = None
    agi: float | None = None


class Adjustments(BaseModel):
    educator_expenses: float | None = None
    hsa_deduction: float | None = None
    se_tax_deduction: float | None = None
    se_retirement: float | None = None
    se_health_insurance: float | None = None
    ira_deduction: float | None = None
    student_loan_interest: float | None = None
    total_adjustments: float | None = None


class Deduction(BaseModel):
    type: Literal["standard", "itemized", "unknown"] = "unknown"
    amount: float | None = None
    qbi_deduction: float | None = None
    taxable_income: float | None = None


class CreditsClaimed(BaseModel):
    child_tax_credit: float | None = None
    additional_child_tax_credit: float | None = None
    child_dependent_care: float | None = None
    education: float | None = None
    education_refundable: float | None = None
    retirement_savers: float | None = None
    residential_energy: float | None = None
    clean_vehicle: float | None = None
    earned_income_credit: float | None = None
    premium_tax_credit: float | None = None


class Payments(BaseModel):
    federal_withholding: float | None = None
    estimated_payments: float | None = None
    total_payments: float | None = None


class TaxSummary(BaseModel):
    total_tax: float | None = None
    refund: float | None = None
    amount_owed: float | None = None


class Questionnaire(BaseModel):
    paid_childcare: Answer = "unknown"
    paid_college_tuition: Answer = "unknown"
    made_ira_contributions: Answer = "unknown"
    made_hsa_contributions: Answer = "unknown"
    made_energy_improvements: Answer = "unknown"
    made_ev_purchase: Answer = "unknown"
    has_medical_expenses: Answer = "unknown"
    has_mortgage_interest: Answer = "unknown"
    made_charitable_gifts: Answer = "unknown"
    has_home_office: Answer = "unknown"


class TaxReviewInput(BaseModel):
    """Structured, redacted summary of the return. Every leaf is a valid `evidence_fields` path."""

    tax_year: int
    filing_status: FilingStatus = "unknown"
    state: str = "unknown"
    taxpayer: PersonProfile = Field(default_factory=PersonProfile)
    spouse: PersonProfile | None = None
    dependents: list[DependentProfile] = Field(default_factory=list)
    income: Income = Field(default_factory=Income)
    adjustments: Adjustments = Field(default_factory=Adjustments)
    deduction: Deduction = Field(default_factory=Deduction)
    credits_claimed: CreditsClaimed = Field(default_factory=CreditsClaimed)
    payments: Payments = Field(default_factory=Payments)
    tax: TaxSummary = Field(default_factory=TaxSummary)
    forms_present: list[str] = Field(default_factory=list)
    user_context: Questionnaire = Field(default_factory=Questionnaire)
    unreadable_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM output (§6.5) and the stored report document
# ---------------------------------------------------------------------------

FindingStatus = Literal["likely_missed", "needs_info", "looks_correct", "possible_error"]


class EstimatedImpact(BaseModel):
    low: float
    high: float
    basis: str


class Finding(BaseModel):
    id: str
    category: Literal["credit", "deduction", "adjustment", "error", "info"]
    title: str
    status: FindingStatus
    explanation: str
    evidence_fields: list[str]
    rule_reference: str
    estimated_impact: EstimatedImpact
    confidence: Literal["high", "medium", "low"]
    questions_for_user: list[str] = Field(default_factory=list)
    next_step: str


class ReviewResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    overall_notes: str = ""


class ReportDocument(ReviewResult):
    """What is stored in `reports.findings`: the validated review plus the context the report needs."""

    tax_year: int
    filing_status: FilingStatus = "unknown"
    unreadable_fields: list[str] = Field(default_factory=list)
    rejected_findings: int = 0


# ---------------------------------------------------------------------------
# API schemas
# ---------------------------------------------------------------------------


class CreateJobRequest(BaseModel):
    tax_year: int = Field(ge=2000, le=2100)
    questionnaire: Questionnaire = Field(default_factory=Questionnaire)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    tax_year: int
    page_count: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class AccessRequest(BaseModel):
    code: str = Field(max_length=256)


class AccessResponse(BaseModel):
    ok: bool


class ConfigResponse(BaseModel):
    auth_mode: str
    retention_hours: int
    default_tax_year: int
    max_upload_mb: int
    max_pages: int
    llm_model: str
    source_code_url: str | None = None
