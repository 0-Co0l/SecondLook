from __future__ import annotations

from pydantic import BaseModel, Field

from .models import Job


class PersonProfile(BaseModel):
    age_bracket: str = "24-64"
    blind: bool = False


class DependentProfile(BaseModel):
    id: str
    relationship: str = "unknown"
    age_bracket: str = "unknown"
    months_in_home: int = 0
    student: bool = False
    disabled: bool = False


class DeductionData(BaseModel):
    type: str = "standard"


class TaxReviewInput(BaseModel):
    tax_year: int
    filing_status: str = "unknown"
    state: str = "unknown"
    taxpayer: PersonProfile = Field(default_factory=PersonProfile)
    spouse: PersonProfile | None = None
    dependents: list[DependentProfile] = Field(default_factory=list)
    income: dict[str, float] = Field(default_factory=dict)
    adjustments: dict[str, float] = Field(default_factory=dict)
    deduction: DeductionData = Field(default_factory=DeductionData)
    credits_claimed: dict[str, float] = Field(default_factory=dict)
    payments: dict[str, float] = Field(default_factory=dict)
    forms_present: list[str] = Field(default_factory=list)
    user_context: dict[str, str] = Field(default_factory=dict)
    unreadable_fields: list[str] = Field(default_factory=list)


def build_review_input(job: Job) -> TaxReviewInput:
    return TaxReviewInput(
        tax_year=job.tax_year,
        filing_status="unknown",
        state="unknown",
        user_context={k: str(v) for k, v in job.questionnaire.items()},
    )
