"""HTML and Markdown downloads (§7), rendered from the same stored document the React page reads."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from .models import Finding, ReportDocument

DISCLAIMER = (
    "SecondLook is an educational review aid, not tax preparation or tax advice. Confirm every finding with a "
    "qualified tax professional (CPA, enrolled agent, or tax attorney) before amending or filing."
)

NEXT_STEPS = [
    "Gather the documents named in each finding (receipts, Forms 1098/1099, provider statements).",
    "Answer the open questions under “Needs more information”.",
    "Talk to a qualified tax professional before amending or filing.",
    "Amended returns have deadlines: generally 3 years from the date you filed or 2 years from the date you paid "
    "the tax, whichever is later. Confirm the deadline for your situation with the IRS or a professional.",
]

SECTIONS = (
    ("likely_missed", "Likely missed"),
    ("needs_info", "Needs more information"),
    ("possible_error", "Possible errors"),
    ("looks_correct", "Looks correct"),
)

FILING_STATUS_LABELS = {
    "single": "Single",
    "married_filing_jointly": "Married filing jointly",
    "married_filing_separately": "Married filing separately",
    "head_of_household": "Head of household",
    "qualifying_surviving_spouse": "Qualifying surviving spouse",
    "unknown": "Not detected",
}


def _money(value: float) -> str:
    return f"−${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def _range(low: float, high: float) -> str:
    return _money(low) if low == high else f"{_money(low)} to {_money(high)}"


def savings_range(findings: list[Finding], status: str) -> tuple[float, float]:
    chosen = [f for f in findings if f.status == status]
    return sum(f.estimated_impact.low for f in chosen), sum(f.estimated_impact.high for f in chosen)


def _by_status(report: ReportDocument) -> dict[str, list[Finding]]:
    grouped: dict[str, list[Finding]] = {status: [] for status, _ in SECTIONS}
    for finding in report.findings:
        grouped.setdefault(finding.status, []).append(finding)
    return grouped


def render_markdown(report: ReportDocument, review_date: datetime | None = None) -> str:
    review_date = review_date or datetime.now(UTC)
    grouped = _by_status(report)
    missed = savings_range(report.findings, "likely_missed")
    info = savings_range(report.findings, "needs_info")
    lines = [
        "# SecondLook tax return review",
        "",
        f"- **Tax year:** {report.tax_year}",
        f"- **Filing status:** {FILING_STATUS_LABELS.get(report.filing_status, report.filing_status)}",
        f"- **Review date:** {review_date.date().isoformat()}",
        "",
        f"> {DISCLAIMER}",
        "",
        "## At a glance",
        "",
    ]
    for status, title in SECTIONS:
        lines.append(f"- {title}: {len(grouped.get(status, []))}")
    lines += [
        "",
        f"Estimated savings from likely missed items: **{_range(*missed)}**",
        "",
        f"Possible additional savings if open questions apply: {_range(*info)} (not included above)",
        "",
    ]
    for status, title in SECTIONS:
        lines += [f"## {title}", ""]
        entries = grouped.get(status, [])
        if not entries:
            lines += ["None.", ""]
            continue
        for finding in entries:
            if status == "looks_correct":
                lines.append(f"- **{finding.title}** — {finding.explanation}")
                continue
            lines += [
                f"### {finding.title}",
                "",
                finding.explanation,
                "",
                f"- Estimated impact: {_range(finding.estimated_impact.low, finding.estimated_impact.high)} "
                f"({finding.confidence} confidence) — {finding.estimated_impact.basis}",
                f"- Next step: {finding.next_step}",
                f"- Rule: `{finding.rule_reference}`",
            ]
            if finding.questions_for_user:
                lines.append("- Questions:")
                lines += [f"  - {q}" for q in finding.questions_for_user]
            lines.append("")
        if status == "looks_correct":
            lines.append("")
    lines += ["## What we couldn't read", ""]
    lines += [f"- {item}" for item in report.unreadable_fields] or ["Everything we looked for was readable."]
    lines += ["", "## Next steps", ""]
    lines += [f"{n}. {step}" for n, step in enumerate(NEXT_STEPS, start=1)]
    if report.overall_notes:
        lines += ["", "## Reviewer notes", "", report.overall_notes]
    lines += ["", "## Disclaimer", "", DISCLAIMER, ""]
    return "\n".join(lines)


HTML_STYLE = """
:root { color-scheme: light dark; --fg:#0f172a; --muted:#475569; --bg:#ffffff; --card:#f8fafc; --line:#e2e8f0;
  --accent:#0f766e; --warn:#9a3412; }
@media (prefers-color-scheme: dark) { :root { --fg:#e2e8f0; --muted:#94a3b8; --bg:#0b1220; --card:#111a2e;
  --line:#1e293b; --accent:#2dd4bf; --warn:#fdba74; } }
body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; line-height: 1.55; color: var(--fg);
  background: var(--bg); max-width: 860px; margin: 2rem auto; padding: 0 16px; }
h1 { margin-bottom: .25rem; } h2 { border-bottom: 1px solid var(--line); padding-bottom: .3rem; margin-top: 2rem; }
.meta { color: var(--muted); } .disclaimer { color: var(--warn); border-left: 3px solid var(--warn); padding-left: .75rem; }
.glance { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .5rem; padding: 0; list-style: none; }
.glance li { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: .5rem .75rem; }
.glance strong { display: block; font-size: 1.4rem; }
.finding { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: .75rem 1rem; margin: .75rem 0; }
.finding h3 { margin: 0 0 .4rem; } .impact { font-weight: 600; color: var(--accent); }
code { font-size: .85em; } @media print { body { margin: 0; } .finding { break-inside: avoid; } }
"""


def _finding_html(finding: Finding) -> str:
    questions = ""
    if finding.questions_for_user:
        questions = "<p>Questions:</p><ul>" + "".join(f"<li>{escape(q)}</li>" for q in finding.questions_for_user) + "</ul>"
    impact = _range(finding.estimated_impact.low, finding.estimated_impact.high)
    return (
        '<article class="finding">'
        f"<h3>{escape(finding.title)}</h3>"
        f"<p>{escape(finding.explanation)}</p>"
        f'<p><span class="impact">Estimated impact: {escape(impact)}</span> '
        f"({escape(finding.confidence)} confidence) — {escape(finding.estimated_impact.basis)}</p>"
        f"<p>Next step: {escape(finding.next_step)}</p>"
        f"{questions}"
        f'<p class="meta">Rule: <code>{escape(finding.rule_reference)}</code></p>'
        "</article>"
    )


def render_html(report: ReportDocument, review_date: datetime | None = None) -> str:
    review_date = review_date or datetime.now(UTC)
    grouped = _by_status(report)
    missed = savings_range(report.findings, "likely_missed")
    info = savings_range(report.findings, "needs_info")
    parts = [
        "<h1>SecondLook tax return review</h1>",
        f'<p class="meta">Tax year {report.tax_year} · '
        f"{escape(FILING_STATUS_LABELS.get(report.filing_status, report.filing_status))} · "
        f"Reviewed {review_date.date().isoformat()}</p>",
        f'<p class="disclaimer">{escape(DISCLAIMER)}</p>',
        "<h2>At a glance</h2>",
        '<ul class="glance">'
        + "".join(f"<li><strong>{len(grouped.get(s, []))}</strong>{escape(t)}</li>" for s, t in SECTIONS)
        + "</ul>",
        f"<p>Estimated savings from likely missed items: <strong>{escape(_range(*missed))}</strong></p>",
        f'<p class="meta">Possible additional savings if open questions apply: {escape(_range(*info))} '
        "(not included above)</p>",
    ]
    for status, title in SECTIONS:
        parts.append(f"<h2>{escape(title)}</h2>")
        entries = grouped.get(status, [])
        if not entries:
            parts.append("<p>None.</p>")
        elif status == "looks_correct":
            parts.append(
                "<ul>"
                + "".join(f"<li><strong>{escape(f.title)}</strong> — {escape(f.explanation)}</li>" for f in entries)
                + "</ul>"
            )
        else:
            parts.extend(_finding_html(f) for f in entries)
    parts.append("<h2>What we couldn't read</h2>")
    if report.unreadable_fields:
        parts.append("<ul>" + "".join(f"<li>{escape(i)}</li>" for i in report.unreadable_fields) + "</ul>")
    else:
        parts.append("<p>Everything we looked for was readable.</p>")
    parts.append("<h2>Next steps</h2><ol>" + "".join(f"<li>{escape(s)}</li>" for s in NEXT_STEPS) + "</ol>")
    if report.overall_notes:
        parts.append(f"<h2>Reviewer notes</h2><p>{escape(report.overall_notes)}</p>")
    parts.append(f"<h2>Disclaimer</h2><p>{escape(DISCLAIMER)}</p>")
    body = "\n".join(parts)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Tax review {report.tax_year}</title>\n<style>{HTML_STYLE}</style>\n</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )
