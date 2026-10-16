import { describe, expect, it } from "vitest";

import type { Finding } from "./api";
import { dragToBox, errorMessage, moneyRange, savingsRange } from "./lib";

const finding = (status: Finding["status"], low: number, high: number): Finding => ({
  id: `${status}-${low}`,
  category: "credit",
  title: "t",
  status,
  explanation: "e",
  evidence_fields: [],
  rule_reference: "rules/2025.json#x",
  estimated_impact: { low, high, basis: "b" },
  confidence: "medium",
  questions_for_user: [],
  next_step: "n",
});

describe("money", () => {
  it("formats ranges and negatives", () => {
    expect(moneyRange(0, 1050)).toBe("$0 to $1,050");
    expect(moneyRange(500, 500)).toBe("$500");
    expect(moneyRange(-200, 0)).toBe("−$200 to $0");
  });

  it("keeps likely-missed savings separate from needs-info", () => {
    const findings = [finding("likely_missed", 100, 300), finding("likely_missed", 0, 50), finding("needs_info", 0, 999)];
    expect(savingsRange(findings, "likely_missed")).toEqual({ low: 100, high: 350 });
    expect(savingsRange(findings, "needs_info")).toEqual({ low: 0, high: 999 });
  });
});

describe("dragToBox", () => {
  const size = { width: 612, height: 792 };
  const rendered = { width: 306, height: 396 };

  it("converts displayed pixels to PDF points", () => {
    expect(dragToBox(1, size, rendered, { x: 10, y: 20 }, { x: 60, y: 30 })).toEqual({ page: 1, x0: 20, y0: 40, x1: 120, y1: 60 });
  });

  it("normalizes reversed drags and clamps to the page", () => {
    expect(dragToBox(0, size, rendered, { x: 400, y: 50 }, { x: 300, y: 10 })).toEqual({ page: 0, x0: 600, y0: 20, x1: 612, y1: 100 });
  });

  it("ignores clicks", () => {
    expect(dragToBox(0, size, rendered, { x: 5, y: 5 }, { x: 5.5, y: 5.2 })).toBeNull();
  });
});

describe("errorMessage", () => {
  it("never shows a blank message", () => {
    expect(errorMessage(undefined)).toBeTruthy();
    expect(errorMessage("pdf_password_protected")).toMatch(/password/);
    expect(errorMessage("something_new")).toContain("something_new");
  });
});
