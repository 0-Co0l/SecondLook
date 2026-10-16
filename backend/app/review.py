from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from pydantic import BaseModel, Field, ValidationError

from .fields import TaxReviewInput
from .pii import scrub_text
from .settings import settings


RULES_PATH = Path(__file__).resolve().parents[1] / "rules"


class EstimatedImpact(BaseModel):
    low: float
    high: float
    basis: str


class Finding(BaseModel):
    id: str
    category: str
    title: str
    status: str
    explanation: str
    evidence_fields: list[str]
    rule_reference: str
    estimated_impact: EstimatedImpact
    confidence: str
    questions_for_user: list[str] = Field(default_factory=list)
    next_step: str


class ReviewResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    overall_notes: str = ""


class LlmClient:
    def analyze(self, payload: TaxReviewInput, rules: dict[str, Any]) -> tuple[ReviewResult, int, int]:
        raise NotImplementedError


class AnthropicLlmClient(LlmClient):
    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required to run LLM review")
        self.client = Anthropic(api_key=settings.anthropic_api_key)

    def analyze(self, payload: TaxReviewInput, rules: dict[str, Any]) -> tuple[ReviewResult, int, int]:
        schema = ReviewResult.model_json_schema()
        system_prompt = (
            "You are a conservative U.S. tax return reviewer. "
            "Use only the provided rules for numeric thresholds. "
            "Never invent facts, and produce only valid JSON matching the schema."
        )
        message = {
            "input": payload.model_dump(mode="json"),
            "rules": rules,
            "schema": schema,
        }
        response = self.client.messages.create(
            model=settings.llm_model,
            max_tokens=1800,
            system=system_prompt,
            messages=[{"role": "user", "content": json.dumps(message)}],
        )
        text_chunks = [block.text for block in response.content if getattr(block, "type", "") == "text"]
        text = "".join(text_chunks)
        try:
            parsed = ReviewResult.model_validate_json(text)
        except ValidationError:
            retry_prompt = (
                "Your previous response failed schema validation. "
                "Return JSON only with exact schema fields."
            )
            retry = self.client.messages.create(
                model=settings.llm_model,
                max_tokens=1800,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": json.dumps(message)},
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": retry_prompt},
                ],
            )
            retry_text = "".join(
                block.text for block in retry.content if getattr(block, "type", "") == "text"
            )
            parsed = ReviewResult.model_validate_json(retry_text)
            usage_in = retry.usage.input_tokens
            usage_out = retry.usage.output_tokens
            return scrub_review_pii(parsed), usage_in, usage_out

        usage_in = response.usage.input_tokens
        usage_out = response.usage.output_tokens
        return scrub_review_pii(parsed), usage_in, usage_out


class DeterministicLlmClient(LlmClient):
    def analyze(self, payload: TaxReviewInput, rules: dict[str, Any]) -> tuple[ReviewResult, int, int]:
        findings: list[Finding] = []
        if payload.user_context.get("paid_childcare") == "yes":
            findings.append(
                Finding(
                    id="F1",
                    category="credit",
                    title="Child and Dependent Care Credit may be unclaimed",
                    status="needs_info",
                    explanation="You indicated childcare costs; verify if qualifying expenses were claimed.",
                    evidence_fields=["user_context.paid_childcare", "credits_claimed.child_dependent_care"],
                    rule_reference=f"rules/{payload.tax_year}.json#child_dependent_care",
                    estimated_impact=EstimatedImpact(low=0, high=2000, basis="Conservative placeholder range"),
                    confidence="medium",
                    questions_for_user=["How much did you pay in qualifying childcare expenses?"],
                    next_step="Review Form 2441 eligibility and expenses paid.",
                )
            )
        result = ReviewResult(
            findings=findings,
            overall_notes="This is an educational review aid, not tax advice.",
        )
        return result, 0, 0


def load_rules(tax_year: int) -> tuple[dict[str, Any], str]:
    path = RULES_PATH / f"{tax_year}.json"
    if not path.exists():
        path = RULES_PATH / "2025.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    rules_version = data.get("version", path.stem)
    return data, str(rules_version)


def build_llm_client() -> LlmClient:
    if settings.anthropic_api_key:
        return AnthropicLlmClient()
    return DeterministicLlmClient()


def scrub_review_pii(result: ReviewResult) -> ReviewResult:
    clean_findings: list[Finding] = []
    for finding in result.findings:
        clean_findings.append(
            finding.model_copy(
                update={
                    "title": scrub_text(finding.title),
                    "explanation": scrub_text(finding.explanation),
                    "next_step": scrub_text(finding.next_step),
                    "questions_for_user": [scrub_text(q) for q in finding.questions_for_user],
                }
            )
        )
    return ReviewResult(findings=clean_findings, overall_notes=scrub_text(result.overall_notes))
