from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.models import (
    CreditsClaimed,
    DependentProfile,
    EstimatedImpact,
    Finding,
    Income,
    Questionnaire,
    ReviewResult,
    TaxReviewInput,
)
from app.review import AnthropicLlmClient, LlmError, finalize_review, load_rules


def _payload() -> TaxReviewInput:
    return TaxReviewInput(
        tax_year=2025,
        filing_status="married_filing_jointly",
        dependents=[DependentProfile(id="DEPENDENT_1", relationship="daughter", age_bracket="under 17")],
        income=Income(wages=90000, agi=88000),
        credits_claimed=CreditsClaimed(child_tax_credit=2200),
        user_context=Questionnaire(paid_childcare="yes"),
    )


def _finding(**overrides: Any) -> Finding:
    values: dict[str, Any] = {
        "id": "F1",
        "category": "credit",
        "title": "Child care credit",
        "status": "likely_missed",
        "explanation": "You paid for care.",
        "evidence_fields": ["user_context.paid_childcare", "credits_claimed.child_dependent_care"],
        "rule_reference": "rules/2025.json#child_dependent_care",
        "estimated_impact": EstimatedImpact(low=5000, high=99999, basis="model guess"),
        "confidence": "high",
        "next_step": "See Form 2441.",
    }
    values.update(overrides)
    return Finding(**values)


def test_rejects_findings_citing_unknown_fields() -> None:
    rules, _ = load_rules(2025)
    result = ReviewResult(findings=[_finding(), _finding(id="F2", evidence_fields=["income.made_up_field"])])
    document = finalize_review(result, _payload(), rules)
    assert [f.id for f in document.findings] == ["F1"]
    assert document.rejected_findings == 1


def test_empty_containers_are_still_citable() -> None:
    rules, _ = load_rules(2025)
    payload = _payload()
    finding = _finding(evidence_fields=["dependents[0].age_bracket", "spouse.age_bracket", "income.capital_gain"])
    assert finalize_review(ReviewResult(findings=[finding]), payload, rules).findings


def test_dollar_estimates_are_recomputed_from_rules() -> None:
    rules, _ = load_rules(2025)
    document = finalize_review(ReviewResult(findings=[_finding()]), _payload(), rules)
    impact = document.findings[0].estimated_impact
    care = rules["rules"]["child_dependent_care"]
    assert impact.low == 0
    assert impact.high == round(care["max_expense_one_qualifying_person"] * care["credit_rate_max"])
    assert "computed from rules file" in impact.basis


def test_missing_rule_downgrades_confidence() -> None:
    rules, _ = load_rules(2025)
    finding = _finding(rule_reference="rules/2025.json#not_a_rule")
    document = finalize_review(ReviewResult(findings=[finding]), _payload(), rules)
    assert document.findings[0].confidence == "low"


def test_output_is_scrubbed_of_pii() -> None:
    rules, _ = load_rules(2025)
    finding = _finding(explanation="Quillfeather's SSN 123-45-6789 and dorian@example.net")
    document = finalize_review(ReviewResult(findings=[finding]), _payload(), rules, ["Quillfeather"])
    text = document.findings[0].explanation
    assert "Quillfeather" not in text and "123-45-6789" not in text and "dorian@example.net" not in text


def test_every_rule_has_a_source() -> None:
    rules, version = load_rules(2025)
    assert version
    for key, rule in rules["rules"].items():
        assert rule.get("source"), key
        assert "verified_at" in rule, key


# ---------------------------------------------------------------------------
# Anthropic client, against a stub of the SDK
# ---------------------------------------------------------------------------


class _StubMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(parsed: Any, stop_reason: str = "end_turn", text: str = "{}") -> Any:
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=900)
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=usage,
        model="claude-sonnet-5-5",
        content=[SimpleNamespace(type="text", text=text)],
    )


def _client(make_settings: Any, responses: list[Any]) -> tuple[AnthropicLlmClient, _StubMessages]:
    settings = make_settings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="test-key")
    client = AnthropicLlmClient(settings)
    stub = _StubMessages(responses)
    client.client = SimpleNamespace(beta=SimpleNamespace(messages=stub))  # type: ignore[assignment]
    return client, stub


def test_request_shape(make_settings: Any) -> None:
    client, stub = _client(make_settings, [_response(ReviewResult(findings=[_finding()]))])
    rules, _ = load_rules(2025)
    response = client.analyze(_payload(), rules)

    call = stub.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_format"] is ReviewResult
    assert call["inference_geo"] == "us"
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    # System prompt and rules come first, with the cache breakpoint on the rules block (§6.4).
    assert call["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert '"child_dependent_care"' in call["system"][-1]["text"]
    assert response.input_tokens == 1000 and response.output_tokens == 50


def test_retries_once_with_validation_error(make_settings: Any) -> None:
    client, stub = _client(make_settings, [_response(None), _response(ReviewResult(findings=[_finding()]))])
    response = client.analyze(_payload(), load_rules(2025)[0])
    assert len(stub.calls) == 2
    retry_messages = stub.calls[1]["messages"]
    assert retry_messages[-1]["role"] == "user" and "schema" in retry_messages[-1]["content"]
    assert response.result.findings


def test_refusal_is_an_error(make_settings: Any) -> None:
    client, _ = _client(make_settings, [_response(None, stop_reason="refusal")])
    with pytest.raises(LlmError) as raised:
        client.analyze(_payload(), load_rules(2025)[0])
    assert raised.value.code == "llm_refused"
