import { describe, expect, it } from "vitest";

import type { TaskEvent } from "../../shared/executionTypes";
import {
  buildTaskTechnicalEntries,
  EMPTY_TECHNICAL_DETAILS_MESSAGE,
  groupTechnicalDetails,
  sanitizeTechnicalText,
  technicalDetailsEmptyState
} from "./technicalDetails";

describe("technical details", () => {
  it("classifies task diagnostics into the four progressive disclosure groups", () => {
    const task: TaskEvent = {
      id: "task-1",
      runId: "run-2",
      title: "检查电脑",
      description: "只读检查",
      state: "blocked",
      agent: "computer_agent",
      createdAt: "2026-07-10T08:00:00.000Z",
      updatedAt: "2026-07-10T08:02:00.000Z",
      boundaryEvents: [],
      recordings: []
    };

    const groups = groupTechnicalDetails(buildTaskTechnicalEntries(task));

    expect(groups.map((group) => group.category)).toEqual([
      "execution",
      "permissions",
      "evidence",
      "diagnostics"
    ]);
    expect(groups.find((group) => group.category === "permissions")?.items).toContainEqual({
      category: "permissions",
      label: "审批状态",
      value: "等待审批"
    });
  });

  it("redacts credentials, user directories and token-like query values", () => {
    const raw = [
      "Authorization: Bearer abc.def.ghi",
      "api_key=sk-super-secret-value",
      "C:\\Users\\Alice\\project\\debug.log",
      "https://example.test/callback?token=secret-token&safe=1",
      "0123456789abcdef0123456789abcdef0123456789abcdef"
    ].join("\n");

    const redacted = sanitizeTechnicalText(raw);

    expect(redacted).not.toContain("abc.def.ghi");
    expect(redacted).not.toContain("super-secret-value");
    expect(redacted).not.toContain("Alice");
    expect(redacted).not.toContain("secret-token");
    expect(redacted).not.toContain("0123456789abcdef0123456789abcdef0123456789abcdef");
    expect(redacted).toContain("C:\\Users\\[用户]");
    expect(redacted).toContain("[已脱敏]");
  });

  it("provides a stable empty state", () => {
    expect(technicalDetailsEmptyState([])).toBe(EMPTY_TECHNICAL_DETAILS_MESSAGE);
    expect(technicalDetailsEmptyState([
      { category: "diagnostics", label: "运行标识", value: "run-1" }
    ])).toBe("");
  });
});
