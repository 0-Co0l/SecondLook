import { FormEvent, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { confirmJob, createJob, getJob, getRedactions } from "../api";

const yesNoUnknown = ["yes", "no", "unknown"] as const;

type Questionnaire = {
  paid_childcare: string;
  paid_college_tuition: string;
  made_ira_contributions: string;
  made_hsa_contributions: string;
  made_energy_improvements: string;
  made_ev_purchase: string;
  has_medical_expenses: string;
  has_mortgage_interest: string;
  made_charitable_gifts: string;
  has_home_office: string;
};

const defaultQuestionnaire: Questionnaire = {
  paid_childcare: "unknown",
  paid_college_tuition: "unknown",
  made_ira_contributions: "unknown",
  made_hsa_contributions: "unknown",
  made_energy_improvements: "unknown",
  made_ev_purchase: "unknown",
  has_medical_expenses: "unknown",
  has_mortgage_interest: "unknown",
  made_charitable_gifts: "unknown",
  has_home_office: "unknown"
};

export function UploadPage() {
  const navigate = useNavigate();
  const [consent, setConsent] = useState(false);
  const [taxYear, setTaxYear] = useState(new Date().getFullYear() - 1);
  const [file, setFile] = useState<File | null>(null);
  const [questionnaire, setQuestionnaire] = useState<Questionnaire>(defaultQuestionnaire);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [redactionLog, setRedactionLog] = useState<Array<Record<string, unknown>>>([]);

  const canSubmit = useMemo(() => consent && file && !loading, [consent, file, loading]);

  async function handleUpload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setLoading(true);
    setStatus("Uploading...");
    try {
      const created = await createJob(file, taxYear, questionnaire);
      setJobId(created.job_id);
      setStatus("Running redaction...");

      const interval = setInterval(async () => {
        try {
          const job = await getJob(created.job_id);
          if (job.status === "awaiting_confirmation") {
            clearInterval(interval);
            const log = await getRedactions(created.job_id);
            setRedactionLog(log);
            setStatus("Review redactions and confirm to start analysis.");
            setLoading(false);
          } else if (job.status === "failed") {
            clearInterval(interval);
            setStatus(`Job failed: ${job.error_code ?? "unknown"}`);
            setLoading(false);
          } else {
            setStatus(`Status: ${job.status}`);
          }
        } catch (error) {
          clearInterval(interval);
          setStatus(error instanceof Error ? error.message : "Failed to poll status");
          setLoading(false);
        }
      }, 1500);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Upload failed");
      setLoading(false);
    }
  }

  async function startReview() {
    if (!jobId) return;
    setLoading(true);
    setStatus("Starting review...");
    try {
      await confirmJob(jobId);
      const interval = setInterval(async () => {
        const job = await getJob(jobId);
        if (job.status === "done") {
          clearInterval(interval);
          navigate(`/report/${jobId}`);
        } else if (job.status === "failed") {
          clearInterval(interval);
          setLoading(false);
          setStatus(`Review failed: ${job.error_code ?? "unknown"}`);
        } else {
          setStatus(`Status: ${job.status}`);
        }
      }, 2000);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Failed to start review");
      setLoading(false);
    }
  }

  return (
    <section>
      <h2>Upload a federal return PDF</h2>
      <p>
        We redact sensitive PII before any LLM review. You must confirm redactions before analysis starts. Data is
        retained for a short period and can be deleted immediately.
      </p>
      <form onSubmit={handleUpload} className="stack">
        <label>
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> I consent to
          redaction and AI-assisted educational review.
        </label>
        <label>
          Tax year
          <input type="number" value={taxYear} onChange={(e) => setTaxYear(Number(e.target.value))} required />
        </label>
        <label>
          PDF file
          <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
        </label>
        <fieldset>
          <legend>Optional questionnaire</legend>
          {(Object.keys(defaultQuestionnaire) as Array<keyof Questionnaire>).map((key) => (
            <label key={key}>
              {key}
              <select
                value={questionnaire[key]}
                onChange={(e) => setQuestionnaire((prev) => ({ ...prev, [key]: e.target.value }))}
              >
                {yesNoUnknown.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </fieldset>
        <button disabled={!canSubmit} type="submit">
          Upload
        </button>
      </form>
      {status && <p>{status}</p>}

      {jobId && redactionLog.length > 0 && (
        <section>
          <h3>Redaction preview</h3>
          <p>
            Original: <img src={`/api/jobs/${jobId}/pages/0?redacted=false`} alt="Original page 1" />
          </p>
          <p>
            Redacted: <img src={`/api/jobs/${jobId}/pages/0?redacted=true`} alt="Redacted page 1" />
          </p>
          <h4>Redaction log</h4>
          <pre>{JSON.stringify(redactionLog, null, 2)}</pre>
          <button onClick={startReview} disabled={loading}>
            Confirm redactions and analyze
          </button>
        </section>
      )}

      <p>
        <Link to="/history">View review history</Link>
      </p>
    </section>
  );
}
