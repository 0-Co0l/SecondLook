import { DragEvent, FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Answer, api, ApiError } from "../api";
import { useConfig } from "../config";
import { ANSWERS, errorMessage, QUESTIONS } from "../lib";

const initialAnswers = Object.fromEntries(QUESTIONS.map((q) => [q.key, "unknown"])) as Record<string, Answer>;

export function UploadPage() {
  const config = useConfig();
  const navigate = useNavigate();
  const [consent, setConsent] = useState(false);
  const [taxYear, setTaxYear] = useState(config.default_tax_year);
  const [file, setFile] = useState<File | null>(null);
  const [answers, setAnswers] = useState(initialAnswers);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => setTaxYear(config.default_tax_year), [config.default_tax_year]);

  function pick(candidate: File | undefined | null) {
    setError("");
    if (!candidate) return;
    if (candidate.type && candidate.type !== "application/pdf") {
      setError(errorMessage("not_a_pdf"));
      return;
    }
    if (candidate.size > config.max_upload_mb * 1024 * 1024) {
      setError(errorMessage("file_too_large"));
      return;
    }
    setFile(candidate);
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    pick(event.dataTransfer.files?.[0]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !consent) return;
    setBusy(true);
    setError("");
    try {
      const { job_id } = await api.createJob(file, taxYear, answers);
      navigate(`/jobs/${job_id}/preview`);
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : undefined));
      setBusy(false);
    }
  }

  return (
    <section className="landing-card">
      <h2>Get a second look at your federal return</h2>
      <p>
        Upload the PDF of a U.S. federal individual return (Form 1040 and its schedules). SecondLook removes your
        personal information first, shows you exactly what it removed, and only after you confirm does it ask an AI
        model to look for deductions and credits that may have been missed — and for possible errors.
      </p>

      <div className="explain-grid">
        <article>
          <h3>What we redact</h3>
          <p>
            Names, Social Security numbers and ITINs, EINs, addresses, phone numbers, email addresses, dates of birth,
            bank account and routing numbers, preparer IDs, Identity Protection PINs, and signatures.
          </p>
        </article>
        <article>
          <h3>What the AI sees</h3>
          <p>
            Only a structured summary of the redacted return: dollar amounts, filing status, which forms are present,
            age ranges in place of birth dates, your state in place of your address, and your answers below. Never the
            PDF itself, and never your name.
          </p>
        </article>
        <article>
          <h3>How long we keep it</h3>
          <p>
            Your original PDF is deleted as soon as you confirm the redactions. Everything else is encrypted and deleted
            automatically after {config.retention_hours} hours, or immediately when you choose “Delete now”.
          </p>
        </article>
      </div>

      <form onSubmit={submit} className="stack form-panel">
        <label
          className={`dropzone${dragging ? " dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input type="file" accept="application/pdf" onChange={(e) => pick(e.target.files?.[0])} />
          <strong>{file ? file.name : "Drop your return PDF here, or choose a file"}</strong>
          <span>
            One PDF, up to {config.max_upload_mb} MB and {config.max_pages} pages. Exported or scanned returns both work.
          </span>
        </label>

        <label className="inline-field">
          Tax year
          <input
            type="number"
            min={2000}
            max={config.default_tax_year}
            value={taxYear}
            onChange={(e) => setTaxYear(Number(e.target.value))}
            required
          />
        </label>

        <fieldset className="questionnaire">
          <legend>A few optional questions (things your return can't show)</legend>
          {QUESTIONS.map((question) => (
            <div key={question.key} className="question-row">
              <span>{question.label}</span>
              <div className="segmented" role="radiogroup" aria-label={question.label}>
                {ANSWERS.map((answer) => (
                  <label key={answer.value} className={answers[question.key] === answer.value ? "selected" : ""}>
                    <input
                      type="radio"
                      name={question.key}
                      value={answer.value}
                      checked={answers[question.key] === answer.value}
                      onChange={() => setAnswers((prev) => ({ ...prev, [question.key]: answer.value }))}
                    />
                    {answer.label}
                  </label>
                ))}
              </div>
            </div>
          ))}
        </fieldset>

        <label className="checkbox-row">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          <span>
            I understand that SecondLook is an educational aid, not tax advice, and I agree to the redaction and AI review
            described above.
          </span>
        </label>

        <button disabled={!consent || !file || busy} type="submit">
          {busy ? "Uploading…" : "Upload and redact"}
        </button>
        {error && (
          <p className="status-banner error" role="alert">
            {error}
          </p>
        )}
      </form>
    </section>
  );
}
