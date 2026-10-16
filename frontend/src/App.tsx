import { Link, Route, Routes } from "react-router-dom";

import { HistoryPage } from "./pages/History";
import { ReportPage } from "./pages/Report";
import { UploadPage } from "./pages/Upload";

export function App() {
  return (
    <div className="app-shell">
      <header className="site-header">
        <p className="eyebrow">Private tax document review workspace</p>
        <h1>SecondLook Tax Return Review</h1>
        <p className="disclaimer">
          Educational review aid only — not tax advice. Confirm all findings with a qualified CPA, EA, or tax
          attorney.
        </p>
        <nav className="site-nav">
          <Link to="/">Upload</Link>
          <Link to="/history">History</Link>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/report/:jobId" element={<ReportPage />} />
        </Routes>
      </main>
    </div>
  );
}
