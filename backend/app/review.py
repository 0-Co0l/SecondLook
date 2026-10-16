"""LLM review (§6): prompt, call, validate, recompute dollar estimates in code."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import EstimatedImpact, Finding, ReportDocument, ReviewResult, TaxReviewInput
from .pii import scrub_text
from .settings import LlmProvider, Settings

logger = logging.getLogger("secondlook.review")

RULES_PATH = Path(__file__).resolve().parents[1] / "rules"
TOP_MARGINAL_RATE = 0.37

SYSTEM_PROMPT = """You are a careful, conservative U.S. federal individual income tax reviewer. You review a \
structured, redacted summary of one Form 1040 return and look for deductions and credits that may have been \
missed or under-claimed, and for possible errors (including errors that would increase the tax owed).

The return summary is JSON. People appear only as tokens such as [TAXPAYER], [SPOUSE] or DEPENDENT_1; never \
try to guess real identities. A null amount means the line was not found on the return, which usually means \
it was blank. `user_context` holds the taxpayer's own yes/no/unknown answers about things the return can't \
show. `unreadable_fields` lists lines that were present but could not be read.

Rules:
- Use only the numbers in the tax-year rules file provided. Never rely on memory for thresholds, limits, rates \
or amounts. If a finding needs a rule that is not in the rules file, say so in the explanation, use \
confidence "low", and give a wide impact range.
- Never invent figures or facts. Every finding must list in `evidence_fields` the exact input paths it relies \
on, written as dotted paths into the input JSON (for example "income.wages", "user_context.paid_childcare", \
"dependents[0].age_bracket").
- `rule_reference` must be "rules/<tax_year>.json#<rule key>" naming the rules-file entry you relied on.
- Classify each item with `status`: "likely_missed" (the return and answers point to an unclaimed benefit), \
"needs_info" (it depends on facts not provided; ask in `questions_for_user`), "looks_correct" (you checked it \
and it is consistent), or "possible_error" (something looks wrong, in either direction).
- Express `estimated_impact` as a conservative low/high range in dollars of tax saved (negative when the \
error would increase tax), with a one-sentence `basis`.
- Take no aggressive or unsupported positions. When unsure, prefer "needs_info" over "likely_missed".
- Include a few "looks_correct" items so the reader sees what was checked.
- Write explanations in plain language first, with form and line references second.
- This is an educational review aid, not tax advice; keep `overall_notes` brief and factual."""


class LlmError(Exception):
    """The LLM call failed; `code` is safe to store as a job error_code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class LlmResponse:
    result: ReviewResult
    input_tokens: int
    output_tokens: int
    model: str


class LlmClient(ABC):
    model: str

    @abstractmethod
    def analyze(self, payload: TaxReviewInput, rules: dict[str, Any]) -> LlmResponse: ...


def _rules_block(rules: dict[str, Any]) -> str:
    # Sorted keys keep the bytes identical across requests, so the cached prefix stays valid.
    return "Tax-year rules file (the only source for numbers):\n" + json.dumps(rules, sort_keys=True, indent=1)


class AnthropicLlmClient(LlmClient):
    def __init__(self, settings: Settings) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=300.0)
        self.model = settings.llm_model
        self.inference_geo = settings.llm_inference_geo or None
        self.refusal_fallback = settings.llm_refusal_fallback

    def analyze(self, payload: TaxReviewInput, rules: dict[str, Any]) -> LlmResponse:
        system = [
            {"type": "text", "text": SYSTEM_PROMPT},
            # Identical for every user, so mark the end of the stable prefix for prompt caching (§6.4).
            {"type": "text", "text": _rules_block(rules), "cache_control": {"type": "ephemeral"}},
        ]
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": "Review this return summary:\n" + payload.model_dump_json(indent=1),
            }
        ]
        input_tokens = output_tokens = 0
        for attempt in range(2):
            response = self._call(system, messages)
            input_tokens += _total_input_tokens(response.usage)
            output_tokens += response.usage.output_tokens
            if response.stop_reason == "refusal":
                raise LlmError("llm_refused")
            if response.stop_reason == "max_tokens":
                raise LlmError("llm_output_truncated")
            try:
                parsed = response.parsed_output
                if parsed is None:
                    raise ValueError("no structured output in response")
                result = ReviewResult.model_validate(parsed.model_dump())
                return LlmResponse(result, input_tokens, output_tokens, response.model)
            except (ValidationError, ValueError) as exc:
                if attempt == 1:
                    raise LlmError("llm_schema_invalid") from exc
                # Retry once with the validation error (§6.6). The assistant turn is replayed as text.
                text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
                messages = [
                    *messages,
                    {"role": "assistant", "content": text or "(empty response)"},
                    {
                        "role": "user",
                        "content": "That response did not match the required schema. Validation error:\n"
                        + _safe_validation_message(exc)
                        + "\nReturn the complete review again, matching the schema exactly.",
                    },
                ]
        raise LlmError("llm_schema_invalid")  # pragma: no cover - loop always returns or raises

    def _call(self, system: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": messages,
            "output_format": ReviewResult,
        }
        betas: list[str] = []
        if self.inference_geo:
            kwargs["inference_geo"] = self.inference_geo
        if self.refusal_fallback:
            betas.append("server-side-fallback-2026-07-01")
            kwargs["fallbacks"] = "default"
        if betas:
            kwargs["betas"] = betas
        anthropic = self._anthropic
        try:
            return self.client.beta.messages.parse(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LlmError("llm_auth_failed") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LlmError("llm_permission_denied") from exc
        except anthropic.RateLimitError as exc:
            raise LlmError("llm_rate_limited") from exc
        except anthropic.BadRequestError as exc:
            raise LlmError("llm_bad_request") from exc
        except anthropic.APIStatusError as exc:
            raise LlmError("llm_server_error" if exc.status_code >= 500 else "llm_request_failed") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmError("llm_unreachable") from exc


def _total_input_tokens(usage: Any) -> int:
    return (
        (usage.input_tokens or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
    )


def _safe_validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "\n".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:20])
    return type(exc).__name__


class FakeLlmClient(LlmClient):
    """Deterministic findings without a network call. Only allowed with AUTH_MODE=none (see settings)."""

    model = "fake"

    def analyze(self, payload: TaxReviewInput, rules: dict[str, Any]) -> LlmResponse:
        year = payload.tax_year
        findings: list[Finding] = []
        if payload.user_context.paid_childcare == "yes" and not payload.credits_claimed.child_dependent_care:
            findings.append(
                Finding(
                    id="F1",
                    category="credit",
                    title="Child and Dependent Care Credit may be unclaimed",
                    status="needs_info",
                    explanation="You said you paid for childcare, but no Child and Dependent Care Credit appears on "
                    "the return. If the care let you work and the child was under 13, part of the cost may qualify.",
                    evidence_fields=["user_context.paid_childcare", "credits_claimed.child_dependent_care"],
                    rule_reference=f"rules/{year}.json#child_dependent_care",
                    estimated_impact=EstimatedImpact(low=0, high=0, basis="placeholder"),
                    confidence="medium",
                    questions_for_user=["How much did you pay for qualifying care, and for how many children?"],
                    next_step="Review Form 2441 and gather provider names and amounts paid.",
                )
            )
        findings.append(
            Finding(
                id=f"F{len(findings) + 1}",
                category="info",
                title="Filing status recorded",
                status="looks_correct",
                explanation="The filing status on the return was read and used for the other checks.",
                evidence_fields=["filing_status"],
                rule_reference=f"rules/{year}.json#standard_deduction",
                estimated_impact=EstimatedImpact(low=0, high=0, basis="no change"),
                confidence="high",
                next_step="No action needed.",
            )
        )
        return LlmResponse(
            ReviewResult(findings=findings, overall_notes="Generated by the offline development reviewer."),
            0,
            0,
            self.model,
        )


def build_llm_client(settings: Settings) -> LlmClient:
    if settings.llm_provider == LlmProvider.FAKE:
        return FakeLlmClient()
    return AnthropicLlmClient(settings)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class RulesMissingError(Exception):
    pass


def available_tax_years() -> list[int]:
    return sorted(int(p.stem) for p in RULES_PATH.glob("*.json") if p.stem.isdigit())


@lru_cache(maxsize=8)
def _load_rules_file(tax_year: int) -> tuple[str, str]:
    path = RULES_PATH / f"{tax_year}.json"
    if not path.is_file():
        raise RulesMissingError(f"no rules file for tax year {tax_year}")
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    return text, str(data.get("version", path.stem))


def load_rules(tax_year: int) -> tuple[dict[str, Any], str]:
    text, version = _load_rules_file(tax_year)
    return json.loads(text), version


# ---------------------------------------------------------------------------
# Validation and recomputation (§6.6)
# ---------------------------------------------------------------------------

_INDEX = re.compile(r"\[\d*\]")


def allowed_evidence_paths(payload: TaxReviewInput) -> set[str]:
    paths: set[str] = set()

    def walk(value: Any, prefix: str) -> None:
        if prefix:
            paths.add(prefix)
        if isinstance(value, dict):
            for key, nested in value.items():
                walk(nested, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, list):
            for item in value:
                walk(item, f"{prefix}[]")

    data = payload.model_dump(mode="json")
    walk(data, "")
    # Every declared field is citable even when absent (e.g. an empty spouse or dependents list).
    for name, field in TaxReviewInput.model_fields.items():
        paths.add(name)
        annotation = field.annotation
        sub_fields = getattr(annotation, "model_fields", None)
        if sub_fields:
            for sub in sub_fields:
                paths.add(f"{name}.{sub}")
    for sub in ("id", "relationship", "age_bracket", "months_in_home", "student", "disabled",
                "child_tax_credit_checked", "other_dependent_credit_checked"):
        paths.add(f"dependents[].{sub}")
    for sub in ("age_bracket", "blind"):
        paths.add(f"spouse.{sub}")
    for dependent in payload.dependents:
        paths.add(f"dependents.{dependent.id}")
    return paths


def _evidence_ok(field: str, allowed: set[str]) -> bool:
    normalized = _INDEX.sub("[]", field.strip())
    if normalized in allowed:
        return True
    # "dependents.DEPENDENT_1.age_bracket" style
    parts = normalized.split(".")
    if len(parts) >= 2 and parts[0] == "dependents":
        return f"dependents.{parts[1]}" in allowed and (len(parts) == 2 or f"dependents[].{parts[2]}" in allowed)
    return False


Calculator = Callable[[TaxReviewInput, dict[str, Any], Finding], tuple[float, float, str] | None]


def _dependents_under_17(payload: TaxReviewInput) -> int:
    return sum(1 for d in payload.dependents if d.age_bracket == "under 17")


def _calc_child_dependent_care(payload: TaxReviewInput, rule: dict[str, Any], finding: Finding):  # type: ignore[no-untyped-def]
    qualifying = max(_dependents_under_17(payload), 1)
    cap = rule["max_expense_two_or_more_qualifying_people"] if qualifying > 1 else rule["max_expense_one_qualifying_person"]
    high = cap * rule["credit_rate_max"] - (payload.credits_claimed.child_dependent_care or 0)
    return 0.0, max(high, 0.0), f"Up to ${cap:,.0f} of care costs × {rule['credit_rate_max']:.0%} maximum rate, less any credit already claimed"


def _calc_child_tax_credit(payload: TaxReviewInput, rule: dict[str, Any], finding: Finding):  # type: ignore[no-untyped-def]
    eligible = _dependents_under_17(payload)
    if not eligible:
        return None
    claimed = (payload.credits_claimed.child_tax_credit or 0) + (payload.credits_claimed.additional_child_tax_credit or 0)
    high = eligible * rule["per_qualifying_child"] - claimed
    return 0.0, max(high, 0.0), f"{eligible} child(ren) under 17 × ${rule['per_qualifying_child']:,.0f}, less credit already claimed"


def _calc_education(payload: TaxReviewInput, rule: dict[str, Any], finding: Finding):  # type: ignore[no-untyped-def]
    students = sum(1 for d in payload.dependents if d.student or d.age_bracket == "17-23")
    students += 1 if payload.taxpayer.age_bracket == "17-23" else 0
    students = max(students, 1)
    claimed = (payload.credits_claimed.education or 0) + (payload.credits_claimed.education_refundable or 0)
    high = students * rule["aotc_max_per_student"] - claimed
    return 0.0, max(high, 0.0), f"Up to ${rule['aotc_max_per_student']:,.0f} per eligible student, less credit already claimed"


def _calc_savers(payload: TaxReviewInput, rule: dict[str, Any], finding: Finding):  # type: ignore[no-untyped-def]
    status = payload.filing_status
    limits = rule["agi_limit"]
    limit = limits.get(status, limits["other"])
    if payload.income.agi is not None and payload.income.agi > limit:
        return 0.0, 0.0, f"AGI is above the ${limit:,.0f} limit for this filing status"
    people = 2 if status == "married_filing_jointly" else 1
    high = people * rule["max_credit_per_person"] - (payload.credits_claimed.retirement_savers or 0)
    return 0.0, max(high, 0.0), f"Up to ${rule['max_credit_per_person']:,.0f} per contributing person, less credit already claimed"


def _calc_eic(payload: TaxReviewInput, rule: dict[str, Any], finding: Finding):  # type: ignore[no-untyped-def]
    children = min(_dependents_under_17(payload), 3)
    maximum = rule["max_credit_by_children"][str(children)]
    high = maximum - (payload.credits_claimed.earned_income_credit or 0)
    return 0.0, max(high, 0.0), f"Maximum credit with {children} qualifying child(ren) is ${maximum:,.0f}"


def _deduction_cap(cap_key: str, per_person: bool = False) -> Calculator:
    def calc(payload: TaxReviewInput, rule: dict[str, Any], finding: Finding):  # type: ignore[no-untyped-def]
        cap = rule[cap_key] * (2 if per_person and payload.filing_status == "married_filing_jointly" else 1)
        return 0.0, cap * TOP_MARGINAL_RATE, f"A ${cap:,.0f} maximum deduction saves at most the top {TOP_MARGINAL_RATE:.0%} rate"

    return calc


def _calc_standard_deduction(payload: TaxReviewInput, rule: dict[str, Any], finding: Finding):  # type: ignore[no-untyped-def]
    standard = rule["values"].get(payload.filing_status)
    if standard is None or payload.deduction.type != "itemized" or payload.deduction.amount is None:
        return None
    gap = standard - payload.deduction.amount
    if gap <= 0:
        return 0.0, 0.0, "Itemized deductions exceed the standard deduction"
    return 0.0, gap * TOP_MARGINAL_RATE, f"Standard deduction exceeds itemized by ${gap:,.0f}; saves at most the top rate on that gap"


CALCULATORS: dict[str, Calculator] = {
    "child_dependent_care": _calc_child_dependent_care,
    "child_tax_credit": _calc_child_tax_credit,
    "education_credits": _calc_education,
    "retirement_savers_credit": _calc_savers,
    "earned_income_credit": _calc_eic,
    "ira_deduction": _deduction_cap("contribution_limit", per_person=True),
    "hsa_deduction": _deduction_cap("family_limit"),
    "student_loan_interest": _deduction_cap("max_deduction"),
    "educator_expenses": _deduction_cap("max_per_educator", per_person=True),
    "standard_deduction": _calc_standard_deduction,
}


def _rule_key(reference: str, tax_year: int) -> str | None:
    match = re.fullmatch(rf"rules/{tax_year}\.json#([a-z0-9_]+)", reference.strip())
    return match.group(1) if match else None


def finalize_review(
    result: ReviewResult,
    payload: TaxReviewInput,
    rules: dict[str, Any],
    known_pii_values: Iterable[str] = (),
) -> ReportDocument:
    """Reject unsupported findings, prefer code-computed impacts, scrub PII from text (§6.6)."""
    allowed = allowed_evidence_paths(payload)
    rule_table: dict[str, Any] = rules.get("rules", {})
    known = list(known_pii_values)
    kept: list[Finding] = []
    rejected = 0
    for finding in result.findings:
        if not finding.evidence_fields or not all(_evidence_ok(f, allowed) for f in finding.evidence_fields):
            rejected += 1
            continue

        updates: dict[str, Any] = {}
        key = _rule_key(finding.rule_reference, payload.tax_year)
        impact = finding.estimated_impact
        low, high = sorted((impact.low, impact.high))
        basis = impact.basis
        if key is None or key not in rule_table:
            updates["confidence"] = "low"
            basis = f"{basis} (cited rule is not in the rules file; model estimate only)"
        elif key in CALCULATORS and finding.status in {"likely_missed", "needs_info"}:
            computed = CALCULATORS[key](payload, rule_table[key], finding)
            if computed is not None:
                low, high, basis = computed[0], computed[1], f"{computed[2]} (computed from rules file)"
        updates["estimated_impact"] = EstimatedImpact(low=round(low), high=round(high), basis=scrub_text(basis, known))

        for name in ("title", "explanation", "next_step"):
            updates[name] = scrub_text(getattr(finding, name), known)
        updates["questions_for_user"] = [scrub_text(q, known) for q in finding.questions_for_user]
        kept.append(finding.model_copy(update=updates))

    if rejected:
        logger.info("rejected %d LLM finding(s) citing unknown input fields", rejected)
    return ReportDocument(
        findings=kept,
        overall_notes=scrub_text(result.overall_notes, known),
        tax_year=payload.tax_year,
        filing_status=payload.filing_status,
        unreadable_fields=payload.unreadable_fields,
        rejected_findings=rejected,
    )
