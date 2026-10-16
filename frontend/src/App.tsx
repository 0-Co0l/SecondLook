import { FormEvent, useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { api, ApiError, AppConfig, AUTH_REQUIRED_EVENT } from "./api";
import { ConfigContext, FALLBACK_CONFIG } from "./config";
import { errorMessage } from "./lib";
import { HistoryPage } from "./pages/History";
import { PreviewPage } from "./pages/Preview";
import { ReportPage } from "./pages/Report";
import { UploadPage } from "./pages/Upload";

export function App() {
  const [config, setConfig] = useState<AppConfig>(FALLBACK_CONFIG);
  const [needsCode, setNeedsCode] = useState(false);

  useEffect(() => {
    void api.config().then(setConfig).catch(() => undefined);
    const onAuth = () => setNeedsCode(true);
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onAuth);
  }, []);

  return (
    <ConfigContext.Provider value={config}>
      <div className="app-shell">
        <header className="site-header">
          <p className="eyebrow">Private tax document review workspace</p>
          <h1>SecondLook Tax Return Review</h1>
          <p className="disclaimer">
            Educational review aid only — not tax preparation or tax advice. Confirm all findings with a qualified CPA,
            enrolled agent, or tax attorney.
          </p>
          <nav className="site-nav">
            <NavLink to="/" end>
              New review
            </NavLink>
            <NavLink to="/history">History</NavLink>
          </nav>
        </header>
        <main>
          {needsCode ? (
            <AccessCodeForm onSuccess={() => window.location.reload()} />
          ) : (
            <Routes>
              <Route path="/" element={<UploadPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/jobs/:jobId/preview" element={<PreviewPage />} />
              <Route path="/jobs/:jobId/report" element={<ReportPage />} />
              <Route path="/report/:jobId" element={<ReportPage />} />
              <Route path="*" element={<section><h2>Page not found</h2></section>} />
            </Routes>
          )}
        </main>
        <footer className="site-footer">
          <p>
            Files are encrypted at rest and deleted automatically after {config.retention_hours} hours, or right away
            with “Delete now”.
          </p>
          {config.source_code_url && (
            <p>
              <a href={config.source_code_url} rel="noreferrer">
                Source code
              </a>{" "}
              (AGPL-3.0)
            </p>
          )}
        </footer>
      </div>
    </ConfigContext.Provider>
  );
}

function AccessCodeForm({ onSuccess }: { onSuccess: () => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.signIn(code);
      onSuccess();
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : undefined));
      setBusy(false);
    }
  }

  return (
    <section className="narrow-card">
      <h2>Enter access code</h2>
      <p>This pilot is limited to invited users. Enter the access code you were given.</p>
      <form className="stack" onSubmit={submit}>
        <label>
          Access code
          <input type="password" autoComplete="current-password" value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        <button type="submit" disabled={busy || !code}>
          Continue
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
