export type JobStatus =
  | "uploaded"
  | "redacting"
  | "awaiting_confirmation"
  | "reviewing"
  | "done"
  | "failed";

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

export async function createJob(file: File, taxYear: number, questionnaire: Record<string, string>) {
  const form = new FormData();
  form.set("file", file);
  form.set("metadata", JSON.stringify({ tax_year: taxYear, questionnaire }));
  const response = await fetch("/api/jobs", { method: "POST", body: form });
  if (!response.ok) {
    throw new Error((await response.json()).error ?? "Failed to upload");
  }
  return (await response.json()) as { job_id: string };
}

export async function listJobs(): Promise<Job[]> {
  const response = await fetch("/api/jobs");
  if (!response.ok) {
    throw new Error((await response.json()).error ?? "Failed to list jobs");
  }
  return response.json();
}

export async function getJob(jobId: string): Promise<Job> {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) {
    throw new Error((await response.json()).error ?? "Failed to load job");
  }
  return response.json();
}

export async function getRedactions(jobId: string): Promise<Array<Record<string, unknown>>> {
  const response = await fetch(`/api/jobs/${jobId}/redactions`);
  if (!response.ok) {
    throw new Error((await response.json()).error ?? "Failed to load redactions");
  }
  return response.json();
}

export async function confirmJob(jobId: string): Promise<void> {
  const response = await fetch(`/api/jobs/${jobId}/confirm`, { method: "POST" });
  if (!response.ok) {
    throw new Error((await response.json()).error ?? "Failed to confirm redactions");
  }
}

export async function getReport(jobId: string): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/jobs/${jobId}/report`);
  if (!response.ok) {
    throw new Error((await response.json()).error ?? "Failed to fetch report");
  }
  return response.json();
}

export async function deleteJob(jobId: string): Promise<void> {
  const response = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error((await response.json()).error ?? "Failed to delete job");
  }
}
