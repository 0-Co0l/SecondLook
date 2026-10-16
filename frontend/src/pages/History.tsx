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
    <section>
      <h2>History</h2>
      {error && <p>{error}</p>}
      <button onClick={() => void refresh()}>Refresh</button>
      <ul>
        {jobs.map((job) => (
          <li key={job.id}>
            <strong>{job.id}</strong> — {job.tax_year} — {job.status}
            {job.status === "done" && <> — <Link to={`/report/${job.id}`}>Open report</Link></>}
            {" — "}
            <button
              onClick={async () => {
                await deleteJob(job.id);
                await refresh();
              }}
            >
              Delete now
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
