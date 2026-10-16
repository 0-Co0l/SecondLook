import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError, Finding, ReportDocument } from "../api";
import { errorMessage, FILING_STATUS_LABELS, FINDING_SECTIONS, formatDate, moneyRange, savingsRange } from "../lib";
import { useJobPolling } from "../usePolling";

const NEXT_STEPS = [
  "Gather the documents named in each finding (receipts, Forms 1098 or 1099, provider statements).",
  "Answer the open questions under “Needs more information”.",
  "Talk to a qualified tax professional before amending or filing.",
  "Amended returns have deadlines: generally 3 years from the date you filed or 2 years from the date you paid the tax, whichever is later. Confirm the deadline for your situation with the IRS or a professional.",
];

export function ReportPage() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const { job, error: pollError } = useJobPolling(jobId, ["done", "awaiting_confirmation"], 2000);
  const [report, setReport] = useState<ReportDocument | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (job?.status === "awaiting_confirmation" && jobId) navigate(`/jobs/${jobId}/preview`, { replace: true });
    if (job?.status !== "done" || !jobId) return;
    void api
      .getReport(jobId)
      .then(setReport)
      .catch((err) => setError(errorMessage(err instanceof ApiError ? err.code : undefined)));
  }, [job?.status, jobId, navigate]);

  async function deleteNow() {
    if (!jobId || !window.confirm("Delete this review and all of its files now? This can't be undone.")) return;
    await api.deleteJob(jobId);
    navigate("/history");
  }

  if (pollError && !job) {
    return (
      <section className="narrow-card">
        <p className="status-banner error">{errorMessage(pollError)}</p>
        <Link to="/history">Back to history</Link>
      </section>
    );
  }

  if (job?.status === "failed") {
    return (
      <section className="narrow-card">
        <h2>The review didn't finish</h2>
        <p className="status-banner error">{errorMessage(job.error_code)}</p>
        <p>Your original file has already been deleted, so please upload it again to retry.</p>
        <Link to="/">Start a new review</Link>
      </section>
    );
  }

  if (!report) {
    return (
      <section className="narrow-card">
        <h2>Reviewing your return…</h2>
        <p>
          The redacted summary is being checked against the {job?.tax_year ?? ""} tax rules. This usually takes under a
          minute.
        </p>
        <div className="progress" aria-label="Working" />
        {error && <p className="status-banner error">{error}</p>}
      </section>
    );
  }

  const missed = savingsRange(report.findings, "likely_missed");
  const info = savingsRange(report.findings, "needs_info");
  const bySection = (status: Finding["status"]) => report.findings.filter((f) => f.status === status);

  return (
    <article className="report-card">
      <header className="report-header">
        <div>
          <h2>Your {report.tax_year} review</h2>
          <p className="meta">
            {FILING_STATUS_LABELS[report.filing_status] ?? report.filing_status}
            {job && <> · Reviewed {formatDate(job.updated_at)}</>}
          </p>
        </div>
        <div className="download-row no-print">
          <a className="button-link" href={`/api/jobs/${jobId}/report.html`} target="_blank" rel="noreferrer">
            HTML
          </a>
          <a className="button-link" href={`/api/jobs/${jobId}/report.md`}>
            Markdown
          </a>
          <button type="button" className="secondary" onClick={() => window.print()}>
            Print / Save as PDF
          </button>
        </div>
      </header>

      <p className="report-disclaimer">
        SecondLook is an educational review aid, not tax preparation or tax advice. Confirm every finding with a
        qualified tax professional (CPA, enrolled agent, or tax attorney) before amending or filing.
      </p>

      <h3>At a glance</h3>
      <ul className="glance">
        {FINDING_SECTIONS.map((section) => (
          <li key={section.status}>
            <strong>{bySection(section.status).length}</strong>
            {section.title}
          </li>
        ))}
      </ul>
      <p>
        Estimated savings from likely missed items: <strong>{moneyRange(missed.low, missed.high)}</strong>
      </p>
      <p className="meta">
        Possible additional savings if open questions apply: {moneyRange(info.low, info.high)} (not included above)
      </p>

      {FINDING_SECTIONS.map((section) => {
        const findings = bySection(section.status);
        return (
          <section key={section.status} className="report-section">
            <h3>{section.title}</h3>
            {findings.length === 0 && <p className="meta">None.</p>}
            {section.status === "looks_correct" ? (
              <ul>
                {findings.map((f) => (
                  <li key={f.id}>
                    <strong>{f.title}</strong> — {f.explanation}
                  </li>
                ))}
              </ul>
            ) : (
              findings.map((f) => <FindingCard key={f.id} finding={f} />)
            )}
          </section>
        );
      })}

      <section className="report-section">
        <h3>What we couldn't read</h3>
        {report.unreadable_fields.length === 0 ? (
          <p className="meta">Everything we looked for was readable.</p>
        ) : (
          <ul>
            {report.unreadable_fields.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="report-section">
        <h3>Next steps</h3>
        <ol>
          {NEXT_STEPS.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </section>

      {report.overall_notes && (
        <section className="report-section">
          <h3>Reviewer notes</h3>
          <p>{report.overall_notes}</p>
        </section>
      )}

      <section className="report-section">
        <h3>Disclaimer</h3>
        <p>
          This report is generated automatically from a redacted summary of your return and may be incomplete or wrong.
          It is not tax advice.
        </p>
      </section>

      <p className="action-row no-print">
        <Link to="/history">Back to history</Link>
        <button type="button" className="danger" onClick={() => void deleteNow()}>
          Delete now
        </button>
      </p>
    </article>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <div className={`finding finding-${finding.status}`}>
      <h4>{finding.title}</h4>
      <p>{finding.explanation}</p>
      <p>
        <span className="impact">
          Estimated impact: {moneyRange(finding.estimated_impact.low, finding.estimated_impact.high)}
        </span>{" "}
        <span className={`confidence confidence-${finding.confidence}`}>{finding.confidence} confidence</span>
      </p>
      <p className="meta">{finding.estimated_impact.basis}</p>
      {finding.questions_for_user.length > 0 && (
        <>
          <p>To check this, we'd need to know:</p>
          <ul>
            {finding.questions_for_user.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </>
      )}
      <p>
        <strong>Next step:</strong> {finding.next_step}
      </p>
      <p className="meta">
        Rule: <code>{finding.rule_reference}</code>
      </p>
    </div>
  );
}
