import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Job, deleteJob, listJobs } from "../api";

export function HistoryPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string>("");

  async function refresh() {
    try {
      setJobs(await listJobs());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load history");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <section className="history-card">
      <h2>History</h2>
      <p>Review prior jobs, open completed reports, and remove records immediately when needed.</p>
      {error && <p className="status-banner">{error}</p>}
      <button onClick={() => void refresh()}>Refresh history</button>
      <ul className="history-list">
        {jobs.map((job) => (
          <li key={job.id} className="history-item">
            <div>
              <p className="history-id">{job.id}</p>
              <p className="history-meta">
                Tax year: <strong>{job.tax_year}</strong> · Status: <strong>{job.status}</strong>
              </p>
            </div>
            <div className="history-actions">
              {job.status === "done" && <Link to={`/report/${job.id}`}>Open report</Link>}
              <button
                onClick={async () => {
                  await deleteJob(job.id);
                  await refresh();
                }}
              >
                Delete now
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
