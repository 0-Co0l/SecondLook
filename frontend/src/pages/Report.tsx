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
    <section>
      <h2>Report</h2>
      <p className="disclaimer">
        Educational review aid only — not tax advice. Confirm with a qualified professional.
      </p>
      {error && <p>{error}</p>}
      {!error && !report && <p>Loading...</p>}
      {report && (
        <>
          <p>
            Downloads:
            {" "}
            <a href={`/api/jobs/${jobId}/report.html`} target="_blank" rel="noreferrer">
              HTML
            </a>
            {" | "}
            <a href={`/api/jobs/${jobId}/report.md`} target="_blank" rel="noreferrer">
              Markdown
            </a>
          </p>
          <pre>{JSON.stringify(report, null, 2)}</pre>
        </>
      )}
      <p>
        <Link to="/history">Back to history</Link>
      </p>
    </section>
  );
}
