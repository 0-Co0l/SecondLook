from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field as SQLField


class JobStatus(StrEnum):
    UPLOADED = "uploaded"
    REDACTING = "redacting"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


class Job(SQLModel, table=True):
    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = SQLField(default="local", index=True)
    status: JobStatus = SQLField(default=JobStatus.UPLOADED, index=True)
    tax_year: int
    page_count: int = 0
    redaction_log: list[dict[str, Any]] = SQLField(default_factory=list, sa_column=Column(JSON))
    questionnaire: dict[str, Any] = SQLField(default_factory=dict, sa_column=Column(JSON))
    error_code: str | None = None
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC) + timedelta(hours=24), index=True)


class Report(SQLModel, table=True):
    job_id: str = SQLField(primary_key=True, foreign_key="job.id")
    model: str
    rules_version: str
    findings: dict[str, Any] = SQLField(default_factory=dict, sa_column=Column(JSON))
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))


class Questionnaire(BaseModel):
    paid_childcare: str = "unknown"
    paid_college_tuition: str = "unknown"
    made_ira_contributions: str = "unknown"
    made_hsa_contributions: str = "unknown"
    made_energy_improvements: str = "unknown"
    made_ev_purchase: str = "unknown"
    has_medical_expenses: str = "unknown"
    has_mortgage_interest: str = "unknown"
    made_charitable_gifts: str = "unknown"
    has_home_office: str = "unknown"


class CreateJobRequest(BaseModel):
    tax_year: int
    questionnaire: Questionnaire = Field(default_factory=Questionnaire)


class JobResponse(BaseModel):
    id: str
    status: JobStatus
    tax_year: int
    page_count: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class PatchRedactionsRequest(BaseModel):
    add: list[dict[str, Any]] = Field(default_factory=list)
    remove_ids: list[str] = Field(default_factory=list)


class AccessRequest(BaseModel):
    code: str


class AccessResponse(BaseModel):
    ok: bool

