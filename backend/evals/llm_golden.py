"""LLM golden set (§11): synthetic return summaries with known missed items and known-correct returns.

Calls the real Claude API (costs money), so it is not part of pytest. Rerun whenever the prompt, model or rules
file changes, and before choosing a model:

    cd backend && ANTHROPIC_API_KEY=... python -m evals.llm_golden [--model claude-sonnet-5-5] [--repeat 3]

Reports true positives, false positives, invented rules and schema failures per case.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from typing import Any

from app.models import (
    CreditsClaimed,
    Deduction,
    DependentProfile,
    Income,
    Payments,
    PersonProfile,
    Questionnaire,
    TaxReviewInput,
)
from app.review import AnthropicLlmClient, LlmError, finalize_review, load_rules
from app.settings import Settings

FLAGGING = {"likely_missed", "needs_info", "possible_error"}


@dataclass
class Case:
    name: str
    payload: TaxReviewInput
    expected_rules: set[str] = field(default_factory=set)  # rule keys that should be flagged
    known_correct: bool = False  # no likely_missed / possible_error expected


def _base(**overrides: Any) -> TaxReviewInput:
    values: dict[str, Any] = {
        "tax_year": 2025,
        "filing_status": "single",
        "state": "OR",
        "taxpayer": PersonProfile(age_bracket="24-64", blind=False),
        "income": Income(wages=60000, taxable_interest=120, total_income=60120, agi=60120),
        "deduction": Deduction(type="standard", amount=15750, taxable_income=44370),
        "payments": Payments(federal_withholding=5200),
        "forms_present": ["1040"],
        "user_context": Questionnaire(**{k: "no" for k in Questionnaire.model_fields}),
    }
    values.update(overrides)
    return TaxReviewInput(**values)


CASES = [
    Case(
        "childcare not claimed",
        _base(
            filing_status="married_filing_jointly",
            spouse=PersonProfile(age_bracket="24-64", blind=False),
            dependents=[DependentProfile(id="DEPENDENT_1", relationship="son", age_bracket="under 17", months_in_home=12)],
            income=Income(wages=85000, total_income=85000, agi=85000),
            deduction=Deduction(type="standard", amount=31500, taxable_income=53500),
            credits_claimed=CreditsClaimed(child_tax_credit=2200),
            user_context=Questionnaire(paid_childcare="yes"),
            forms_present=["1040", "Schedule 8812"],
        ),
        expected_rules={"child_dependent_care"},
    ),
    Case(
        "education credit not claimed",
        _base(
            filing_status="head_of_household",
            dependents=[DependentProfile(id="DEPENDENT_1", relationship="daughter", age_bracket="17-23", student=True)],
            deduction=Deduction(type="standard", amount=23625, taxable_income=36495),
            user_context=Questionnaire(paid_college_tuition="yes"),
        ),
        expected_rules={"education_credits"},
    ),
    Case(
        "saver's credit not claimed",
        _base(
            income=Income(wages=34000, total_income=34000, agi=32000),
            adjustments={"ira_deduction": 2000, "total_adjustments": 2000},
            deduction=Deduction(type="standard", amount=15750, taxable_income=16250),
            user_context=Questionnaire(made_ira_contributions="yes"),
        ),
        expected_rules={"retirement_savers_credit"},
    ),
    Case(
        "child tax credit under-claimed",
        _base(
            filing_status="married_filing_jointly",
            spouse=PersonProfile(age_bracket="24-64", blind=False),
            dependents=[
                DependentProfile(id="DEPENDENT_1", relationship="son", age_bracket="under 17", child_tax_credit_checked=True),
                DependentProfile(id="DEPENDENT_2", relationship="daughter", age_bracket="under 17", child_tax_credit_checked=True),
            ],
            income=Income(wages=110000, total_income=110000, agi=110000),
            deduction=Deduction(type="standard", amount=31500, taxable_income=78500),
            credits_claimed=CreditsClaimed(child_tax_credit=2200),
            forms_present=["1040", "Schedule 8812"],
        ),
        expected_rules={"child_tax_credit"},
    ),
    Case(
        "itemized below standard",
        _base(
            deduction=Deduction(type="itemized", amount=11200, taxable_income=48920),
            forms_present=["1040", "Schedule A"],
        ),
        expected_rules={"standard_deduction"},
    ),
    Case("known correct: simple single filer", _base(), known_correct=True),
]


def _rule_key(reference: str) -> str | None:
    match = re.search(r"#([a-z0-9_]+)$", reference)
    return match.group(1) if match else None


def run(model: str, repeat: int) -> None:
    settings = Settings()
    settings.llm_model = model
    client = AnthropicLlmClient(settings)
    rules, version = load_rules(2025)
    rule_keys = set(rules["rules"])
    totals = {"tp": 0, "fn": 0, "fp": 0, "invented": 0, "schema_failures": 0, "rejected": 0}
    print(f"model={model} rules={version}")
    for case in CASES:
        for attempt in range(repeat):
            try:
                response = client.analyze(case.payload, rules)
            except LlmError as exc:
                totals["schema_failures"] += exc.code == "llm_schema_invalid"
                print(f"  {case.name} #{attempt + 1}: ERROR {exc.code}")
                continue
            invented = [f.rule_reference for f in response.result.findings if _rule_key(f.rule_reference) not in rule_keys]
            document = finalize_review(response.result, case.payload, rules)
            flagged = {_rule_key(f.rule_reference) for f in document.findings if f.status in FLAGGING}
            hard_flags = {
                _rule_key(f.rule_reference) for f in document.findings if f.status in {"likely_missed", "possible_error"}
            }
            tp = len(case.expected_rules & flagged)
            fn = len(case.expected_rules - flagged)
            fp = len(hard_flags - case.expected_rules) if not case.known_correct else len(hard_flags)
            totals["tp"] += tp
            totals["fn"] += fn
            totals["fp"] += fp
            totals["invented"] += len(invented)
            totals["rejected"] += document.rejected_findings
            print(
                f"  {case.name} #{attempt + 1}: tp={tp} fn={fn} fp={fp} invented={len(invented)} "
                f"rejected={document.rejected_findings} tokens={response.input_tokens}/{response.output_tokens}"
            )
    print("totals:", totals)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    run(args.model, args.repeat)
