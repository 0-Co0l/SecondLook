import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getReport } from "../api";

export function ReportPage() {
  const { jobId } = useParams();
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    if (!jobId) return;
    void getReport(jobId)
      .then((payload) => {
        setReport(payload);
        setError("");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load report"));
  }, [jobId]);

  return (
    <section className="report-card">
      <h2>Report</h2>
      <p className="report-intro">
        Structured findings from the redacted return are shown below, with downloadable formats for sharing.
      </p>
      <p className="disclaimer report-disclaimer">
        Educational review aid only — not tax advice. Confirm with a qualified professional.
      </p>
      {error && <p className="status-banner">{error}</p>}
      {!error && !report && <p className="status-banner">Loading report...</p>}
      {report && (
        <>
          <p className="download-row">
            Downloads:
            {" "}
            <a className="download-link" href={`/api/jobs/${jobId}/report.html`} target="_blank" rel="noreferrer">
              HTML
            </a>
            {" | "}
            <a className="download-link" href={`/api/jobs/${jobId}/report.md`} target="_blank" rel="noreferrer">
              Markdown
            </a>
          </p>
          <pre className="report-json">{JSON.stringify(report, null, 2)}</pre>
        </>
      )}
      <p className="back-link-row">
        <Link to="/history">Back to history</Link>
      </p>
    </section>
  );
}
