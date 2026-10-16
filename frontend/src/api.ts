export type JobStatus = "uploaded" | "redacting" | "awaiting_confirmation" | "reviewing" | "done" | "failed";
export type Answer = "yes" | "no" | "unknown";

export type Job = {
  id: string;
  status: JobStatus;
  tax_year: number;
  page_count: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string;
};

export type AppConfig = {
  auth_mode: "none" | "access_code" | "oidc";
  retention_hours: number;
  default_tax_year: number;
  max_upload_mb: number;
  max_pages: number;
  llm_model: string;
  source_code_url: string | null;
};

export type Box = { page: number; x0: number; y0: number; x1: number; y1: number };
export type RedactionRegion = Box & { id: string; kind: string; detector: string; score: number };
export type PageSize = { width: number; height: number };
export type Redactions = { regions: RedactionRegion[]; pages: PageSize[] };

export type FindingStatus = "likely_missed" | "needs_info" | "looks_correct" | "possible_error";
export type Finding = {
  id: string;
  category: "credit" | "deduction" | "adjustment" | "error" | "info";
  title: string;
  status: FindingStatus;
  explanation: string;
  evidence_fields: string[];
  rule_reference: string;
  estimated_impact: { low: number; high: number; basis: string };
  confidence: "high" | "medium" | "low";
  questions_for_user: string[];
  next_step: string;
};
export type ReportDocument = {
  findings: Finding[];
  overall_notes: string;
  tax_year: number;
  filing_status: string;
  unreadable_fields: string[];
  rejected_findings: number;
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

/** Fired on any 401 so the app can show the access-code screen. */
export const AUTH_REQUIRED_EVENT = "secondlook:auth-required";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (response.status === 401) {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  }
  if (!response.ok) {
    let code = `http_${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.error === "string") code = body.error;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(response.status, code);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  config: () => request<AppConfig>("/api/config"),
  signIn: (code: string) => request<{ ok: boolean }>("/api/access", json("POST", { code })),
  createJob(file: File, taxYear: number, questionnaire: Record<string, Answer>) {
    const form = new FormData();
    form.set("file", file);
    form.set("metadata", JSON.stringify({ tax_year: taxYear, questionnaire }));
    return request<{ job_id: string }>("/api/jobs", { method: "POST", body: form });
  },
  listJobs: () => request<Job[]>("/api/jobs"),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  getRedactions: (id: string) => request<Redactions>(`/api/jobs/${id}/redactions`),
  patchRedactions: (id: string, add: Box[], removeIds: string[]) =>
    request<{ regions: RedactionRegion[] }>(`/api/jobs/${id}/redactions`, json("PATCH", { add, remove_ids: removeIds })),
  confirm: (id: string) => request<{ status: string }>(`/api/jobs/${id}/confirm`, { method: "POST" }),
  getReport: (id: string) => request<ReportDocument>(`/api/jobs/${id}/report`),
  deleteJob: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
  pageUrl: (id: string, page: number, redacted: boolean, version = 0) =>
    `/api/jobs/${id}/pages/${page}?redacted=${redacted}&v=${version}`,
};
