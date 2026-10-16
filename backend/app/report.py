from __future__ import annotations

from datetime import UTC, datetime

from .review import ReviewResult


DISCLAIMER = (
    "Educational review aid only — not tax preparation or legal/tax advice. "
    "Please confirm findings with a qualified CPA, EA, or tax attorney before filing or amending."
)


def render_markdown(review: ReviewResult, tax_year: int, filing_status: str) -> str:
    now = datetime.now(UTC).date().isoformat()
    lines = [
        "# Tax Return Review Summary",
        "",
        f"- Tax year: {tax_year}",
        f"- Filing status: {filing_status}",
        f"- Review date: {now}",
        f"- Disclaimer: {DISCLAIMER}",
        "",
    ]

    by_status: dict[str, list] = {}
    for finding in review.findings:
        by_status.setdefault(finding.status, []).append(finding)

    lines.append("## At a glance")
    for status in ("likely_missed", "needs_info", "possible_error", "looks_correct"):
        lines.append(f"- {status}: {len(by_status.get(status, []))}")
    lines.append("")

    for section, title in (
        ("likely_missed", "Likely missed"),
        ("needs_info", "Needs more information"),
        ("possible_error", "Possible errors"),
        ("looks_correct", "Looks correct"),
    ):
        lines.append(f"## {title}")
        entries = by_status.get(section, [])
        if not entries:
            lines.append("- None")
            lines.append("")
            continue
        for finding in entries:
            lines.extend(
                [
                    f"### {finding.title}",
                    f"- Confidence: {finding.confidence}",
                    f"- Estimated impact: ${finding.estimated_impact.low:,.0f} to ${finding.estimated_impact.high:,.0f}",
                    f"- Explanation: {finding.explanation}",
                    f"- Next step: {finding.next_step}",
                ]
            )
            if finding.questions_for_user:
                lines.append("- Questions:")
                for question in finding.questions_for_user:
                    lines.append(f"  - {question}")
            lines.append("")

    lines.append("## Overall notes")
    lines.append(review.overall_notes or "No additional notes.")
    lines.append("")
    lines.append("## Disclaimer")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def render_html(review: ReviewResult, tax_year: int, filing_status: str) -> str:
    markdown = render_markdown(review, tax_year, filing_status)
    escaped = (
        markdown.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Tax Review Report</title>
  <style>
    body {{ font-family: sans-serif; line-height: 1.5; max-width: 900px; margin: 2rem auto; }}
  </style>
</head>
<body>{escaped}</body>
</html>"""
