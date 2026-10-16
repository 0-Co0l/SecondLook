import type { Answer, Box, Finding, FindingStatus, JobStatus, PageSize } from "./api";

const ERROR_MESSAGES: Record<string, string> = {
  not_a_pdf: "That file isn't a PDF.",
  pdf_unreadable: "We couldn't read that PDF. Try exporting it again from your tax software.",
  pdf_password_protected: "That PDF is password-protected. Remove the password and upload it again.",
  pdf_timeout: "That PDF took too long to process. Try a smaller or re-exported file.",
  pdf_empty: "That PDF has no pages.",
  too_many_pages: "That PDF has more pages than we can review at once.",
  file_too_large: "That file is too large.",
  unsupported_tax_year: "We don't have rules for that tax year yet.",
  too_many_requests: "Too many attempts. Wait a few minutes and try again.",
  invalid_access_code: "That access code isn't right.",
  sign_in_required: "Enter the access code to continue.",
  job_not_found: "That review no longer exists. It may have been deleted or expired.",
  report_not_found: "The report isn't ready yet.",
  job_not_awaiting_confirmation: "This review has already moved past the redaction step.",
  page_not_available: "That page image is no longer available.",
  interrupted: "The server restarted while this review was running. Please upload the file again.",
  redaction_failed: "Something went wrong while redacting. Please try again.",
  review_failed: "Something went wrong during the review. Please try again.",
  llm_refused: "The AI reviewer declined to review this return.",
  llm_output_truncated: "The AI review was cut off. Please try again.",
  llm_schema_invalid: "The AI review came back in an unexpected format. Please try again.",
  llm_rate_limited: "The AI service is busy. Please try again in a few minutes.",
  llm_unreachable: "We couldn't reach the AI service. Please try again later.",
};

export function errorMessage(code: string | null | undefined): string {
  if (!code) return "Something went wrong.";
  return ERROR_MESSAGES[code] ?? `Something went wrong (${code}).`;
}

export const STATUS_LABELS: Record<JobStatus, string> = {
  uploaded: "Uploaded",
  redacting: "Redacting",
  awaiting_confirmation: "Waiting for your check",
  reviewing: "Reviewing",
  done: "Report ready",
  failed: "Failed",
};

export const FINDING_SECTIONS: Array<{ status: FindingStatus; title: string }> = [
  { status: "likely_missed", title: "Likely missed" },
  { status: "needs_info", title: "Needs more information" },
  { status: "possible_error", title: "Possible errors" },
  { status: "looks_correct", title: "Looks correct" },
];

export const FILING_STATUS_LABELS: Record<string, string> = {
  single: "Single",
  married_filing_jointly: "Married filing jointly",
  married_filing_separately: "Married filing separately",
  head_of_household: "Head of household",
  qualifying_surviving_spouse: "Qualifying surviving spouse",
  unknown: "Not detected",
};

export const KIND_LABELS: Record<string, string> = {
  name: "Name",
  ssn: "SSN / ITIN",
  ein: "EIN",
  address: "Address",
  location: "Address",
  zip: "ZIP code",
  phone: "Phone",
  email: "Email",
  dob: "Date of birth",
  bank: "Bank number",
  ptin: "Preparer PTIN",
  ip_pin: "Identity Protection PIN",
  signature: "Signature",
  employer: "Employer",
  user: "Added by you",
};

export const QUESTIONS: Array<{ key: string; label: string }> = [
  { key: "paid_childcare", label: "Did you pay for childcare or dependent care so you could work?" },
  { key: "paid_college_tuition", label: "Did you pay college tuition or required course fees?" },
  { key: "made_ira_contributions", label: "Did you contribute to a traditional or Roth IRA?" },
  { key: "made_hsa_contributions", label: "Did you contribute to a Health Savings Account (HSA)?" },
  { key: "made_energy_improvements", label: "Did you make energy-efficiency improvements or install solar?" },
  { key: "made_ev_purchase", label: "Did you buy an electric or plug-in hybrid vehicle?" },
  { key: "has_medical_expenses", label: "Did you have large out-of-pocket medical expenses?" },
  { key: "has_mortgage_interest", label: "Did you pay mortgage interest on your home?" },
  { key: "made_charitable_gifts", label: "Did you give to charity?" },
  { key: "has_home_office", label: "Do you use part of your home regularly and only for business?" },
];

export const ANSWERS: Array<{ value: Answer; label: string }> = [
  { value: "unknown", label: "Not sure" },
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
];

export function money(value: number): string {
  const formatted = `$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
  return value < 0 ? `−${formatted}` : formatted;
}

export function moneyRange(low: number, high: number): string {
  return low === high ? money(low) : `${money(low)} to ${money(high)}`;
}

export function savingsRange(findings: Finding[], status: FindingStatus): { low: number; high: number } {
  return findings
    .filter((f) => f.status === status)
    .reduce((total, f) => ({ low: total.low + f.estimated_impact.low, high: total.high + f.estimated_impact.high }), {
      low: 0,
      high: 0,
    });
}

/** Turn a drag on a rendered page image (in displayed pixels) into a box in PDF points. */
export function dragToBox(
  page: number,
  size: PageSize,
  rendered: { width: number; height: number },
  start: { x: number; y: number },
  end: { x: number; y: number },
): Box | null {
  const sx = size.width / rendered.width;
  const sy = size.height / rendered.height;
  const x0 = Math.max(0, Math.min(start.x, end.x) * sx);
  const y0 = Math.max(0, Math.min(start.y, end.y) * sy);
  const x1 = Math.min(size.width, Math.max(start.x, end.x) * sx);
  const y1 = Math.min(size.height, Math.max(start.y, end.y) * sy);
  if (x1 - x0 < 2 || y1 - y0 < 2) return null;
  return { page, x0, y0, x1, y1 };
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
