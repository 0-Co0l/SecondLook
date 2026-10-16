import { PointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { api, ApiError, Box, Redactions } from "../api";
import { dragToBox, errorMessage, KIND_LABELS, STATUS_LABELS } from "../lib";
import { useJobPolling } from "../usePolling";

type Point = { x: number; y: number };

export function PreviewPage() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const { job, error: pollError } = useJobPolling(jobId, ["awaiting_confirmation", "reviewing", "done"]);
  const [redactions, setRedactions] = useState<Redactions | null>(null);
  const [page, setPage] = useState(0);
  const [pendingAdd, setPendingAdd] = useState<Box[]>([]);
  const [pendingRemove, setPendingRemove] = useState<Set<string>>(new Set());
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState<{ start: Point; end: Point } | null>(null);
  const imageRef = useRef<HTMLImageElement>(null);

  const ready = job?.status === "awaiting_confirmation";

  useEffect(() => {
    if (!ready || !jobId) return;
    void api
      .getRedactions(jobId)
      .then(setRedactions)
      .catch((err) => setError(errorMessage(err instanceof ApiError ? err.code : undefined)));
  }, [ready, jobId]);

  const size = redactions?.pages[page];
  const pageRegions = useMemo(() => (redactions?.regions ?? []).filter((r) => r.page === page), [redactions, page]);
  const pageAdds = pendingAdd.filter((b) => b.page === page);
  const dirty = pendingAdd.length > 0 || pendingRemove.size > 0;

  const kindCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const region of redactions?.regions ?? []) {
      if (pendingRemove.has(region.id)) continue;
      const label = KIND_LABELS[region.kind] ?? region.kind;
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [redactions, pendingRemove]);

  if (!jobId) return <Navigate to="/" replace />;
  if (job && (job.status === "reviewing" || job.status === "done")) return <Navigate to={`/jobs/${jobId}/report`} replace />;

  function pointFrom(event: PointerEvent<SVGSVGElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function finishDrag() {
    if (drag && size && imageRef.current) {
      const rendered = { width: imageRef.current.clientWidth, height: imageRef.current.clientHeight };
      const box = dragToBox(page, size, rendered, drag.start, drag.end);
      if (box) setPendingAdd((prev) => [...prev, box]);
    }
    setDrag(null);
  }

  function toggleRemove(id: string) {
    setPendingRemove((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function saveChanges() {
    if (!jobId) return;
    setBusy(true);
    setError("");
    try {
      const { regions } = await api.patchRedactions(jobId, pendingAdd, [...pendingRemove]);
      setRedactions((prev) => (prev ? { ...prev, regions } : prev));
      setPendingAdd([]);
      setPendingRemove(new Set());
      setVersion((v) => v + 1);
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : undefined));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!jobId) return;
    setBusy(true);
    setError("");
    try {
      await api.confirm(jobId);
      navigate(`/jobs/${jobId}/report`);
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : undefined));
      setBusy(false);
    }
  }

  if (pollError && !job) {
    return (
      <section>
        <p className="status-banner error">{errorMessage(pollError)}</p>
        <Link to="/">Start a new review</Link>
      </section>
    );
  }

  if (!job || job.status === "redacting" || job.status === "uploaded") {
    return (
      <section className="narrow-card">
        <h2>Redacting your return…</h2>
        <p>
          We're finding and removing personal information. Scanned pages take longer because they need text
          recognition. Nothing has been sent to the AI.
        </p>
        <div className="progress" aria-label="Working" />
      </section>
    );
  }

  if (job.status === "failed") {
    return (
      <section className="narrow-card">
        <h2>We couldn't finish redacting</h2>
        <p className="status-banner error">{errorMessage(job.error_code)}</p>
        <Link to="/">Start a new review</Link>
      </section>
    );
  }

  return (
    <section className="preview-card">
      <h2>Check what we redacted</h2>
      <p>
        Black boxes on the right are what will be removed. On the left, outlined boxes show the same areas on your
        original. <strong>Click a box</strong> to keep that text (for example, a dollar amount we hid by mistake), or{" "}
        <strong>drag across the original</strong> to hide something we missed. Nothing is sent to the AI until you
        confirm.
      </p>

      <div className="preview-toolbar">
        <button type="button" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
          ← Previous
        </button>
        <span>
          Page {page + 1} of {job.page_count}
        </span>
        <button
          type="button"
          onClick={() => setPage((p) => Math.min(job.page_count - 1, p + 1))}
          disabled={page >= job.page_count - 1}
        >
          Next →
        </button>
        <span className="pill">{STATUS_LABELS[job.status]}</span>
      </div>

      <div className="preview-grid">
        <figure>
          <figcaption>Original (only you can see this)</figcaption>
          <div className="page-frame">
            <img
              ref={imageRef}
              src={api.pageUrl(jobId, page, false)}
              alt={`Original page ${page + 1}`}
              draggable={false}
            />
            {size && (
              <svg
                className="overlay"
                viewBox={`0 0 ${size.width} ${size.height}`}
                preserveAspectRatio="none"
                onPointerDown={(e) => {
                  if ((e.target as Element).tagName === "rect" && !(e.target as Element).classList.contains("drawing")) return;
                  e.currentTarget.setPointerCapture(e.pointerId);
                  const p = pointFrom(e);
                  setDrag({ start: p, end: p });
                }}
                onPointerMove={(e) => drag && setDrag({ ...drag, end: pointFrom(e) })}
                onPointerUp={finishDrag}
                onPointerCancel={() => setDrag(null)}
              >
                {pageRegions.map((region) => (
                  <rect
                    key={region.id}
                    className={pendingRemove.has(region.id) ? "region removed" : "region"}
                    x={region.x0}
                    y={region.y0}
                    width={region.x1 - region.x0}
                    height={region.y1 - region.y0}
                    onClick={() => toggleRemove(region.id)}
                  >
                    <title>
                      {KIND_LABELS[region.kind] ?? region.kind} — {pendingRemove.has(region.id) ? "will be kept" : "click to keep"}
                    </title>
                  </rect>
                ))}
                {pageAdds.map((box, i) => (
                  <rect
                    key={`add-${i}`}
                    className="region added"
                    x={box.x0}
                    y={box.y0}
                    width={box.x1 - box.x0}
                    height={box.y1 - box.y0}
                    onClick={() => setPendingAdd((prev) => prev.filter((b) => b !== box))}
                  >
                    <title>Added by you — click to undo</title>
                  </rect>
                ))}
                {drag && size && imageRef.current && <DragRect drag={drag} size={size} image={imageRef.current} />}
              </svg>
            )}
          </div>
        </figure>
        <figure>
          <figcaption>Redacted{dirty ? " (save changes to update)" : ""}</figcaption>
          <div className="page-frame">
            <img src={api.pageUrl(jobId, page, true, version)} alt={`Redacted page ${page + 1}`} draggable={false} />
          </div>
        </figure>
      </div>

      <div className="redaction-summary">
        <h3>What we found</h3>
        {kindCounts.length === 0 ? (
          <p>No personal information was detected. Check each page carefully and add boxes if needed.</p>
        ) : (
          <ul className="pill-row">
            {kindCounts.map(([label, count]) => (
              <li key={label} className="pill">
                {label}: {count}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="action-row">
        <button type="button" className="secondary" onClick={saveChanges} disabled={!dirty || busy}>
          Save changes
        </button>
        {dirty && (
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setPendingAdd([]);
              setPendingRemove(new Set());
            }}
            disabled={busy}
          >
            Discard changes
          </button>
        )}
        <button type="button" onClick={confirm} disabled={dirty || busy}>
          Confirm redactions and start review
        </button>
      </div>
      {dirty && <p className="hint">Save or discard your changes before confirming.</p>}
      {error && (
        <p className="status-banner error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function DragRect({
  drag,
  size,
  image,
}: {
  drag: { start: Point; end: Point };
  size: { width: number; height: number };
  image: HTMLImageElement;
}) {
  const sx = size.width / image.clientWidth;
  const sy = size.height / image.clientHeight;
  return (
    <rect
      className="region drawing"
      x={Math.min(drag.start.x, drag.end.x) * sx}
      y={Math.min(drag.start.y, drag.end.y) * sy}
      width={Math.abs(drag.end.x - drag.start.x) * sx}
      height={Math.abs(drag.end.y - drag.start.y) * sy}
    />
  );
}
