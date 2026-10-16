import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, Job } from "../api";
import { errorMessage, formatDate, STATUS_LABELS } from "../lib";

function jobLink(job: Job): { to: string; label: string } | null {
  if (job.status === "done") return { to: `/jobs/${job.id}/report`, label: "Open report" };
  if (job.status === "awaiting_confirmation") return { to: `/jobs/${job.id}/preview`, label: "Check redactions" };
  if (job.status === "redacting" || job.status === "reviewing") return { to: `/jobs/${job.id}/preview`, label: "View progress" };
  return null;
}

export function HistoryPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setJobs(await api.listJobs());
      setError("");
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : undefined));
    }
  }

  async function remove(job: Job) {
    if (!window.confirm(`Delete the ${job.tax_year} review and all of its files now? This can't be undone.`)) return;
    try {
      await api.deleteJob(job.id);
      await refresh();
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : undefined));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <section className="history-card">
      <h2>Your reviews</h2>
      <p>Reviews delete themselves automatically when they expire. You can also delete one right away.</p>
      {error && <p className="status-banner error">{error}</p>}
      {jobs && jobs.length === 0 && (
        <p>
          No reviews yet. <Link to="/">Start one</Link>.
        </p>
      )}
      <ul className="history-list">
        {(jobs ?? []).map((job) => {
          const link = jobLink(job);
          return (
            <li key={job.id} className="history-item">
              <div>
                <p className="history-title">
                  {job.tax_year} return · <span className={`status status-${job.status}`}>{STATUS_LABELS[job.status]}</span>
                </p>
                <p className="history-meta">
                  Uploaded {formatDate(job.created_at)} · Deletes {formatDate(job.expires_at)}
                  {job.status === "failed" && <> · {errorMessage(job.error_code)}</>}
                </p>
              </div>
              <div className="history-actions">
                {link && <Link to={link.to}>{link.label}</Link>}
                <button type="button" className="danger" onClick={() => void remove(job)}>
                  Delete now
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
