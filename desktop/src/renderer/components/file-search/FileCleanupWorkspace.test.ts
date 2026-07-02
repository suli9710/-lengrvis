import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CleanupPlan } from "../../../shared/types";
import { buildCleanupPreviewModel, useFileCleanupWorkspace } from "./FileCleanupWorkspace";

function cleanupPlan(overrides: Partial<CleanupPlan> = {}): CleanupPlan {
  return {
    id: "plan-1",
    title: "Cleanup preview",
    riskWarnings: [],
    items: [],
    ...overrides
  };
}

describe("buildCleanupPreviewModel", () => {
  it("classifies cleanup items and exposes the executable approval surface", () => {
    const model = buildCleanupPreviewModel(cleanupPlan({
      items: [
        { id: "delete", path: "C:\\temp\\old.log", action: "delete", disposition: "permanent_delete" },
        { id: "trash", path: "C:\\temp\\draft.txt", action: "trash", disposition: "trash" },
        { id: "suggest", path: "C:\\temp\\review.txt", action: "review", disposition: "suggestion_only" },
        { id: "skip", path: "C:\\temp\\keep.txt", action: "skip", disposition: "skip" }
      ]
    }));

    expect(model.permanent.map((item) => item.id)).toEqual(["delete"]);
    expect(model.trash.map((item) => item.id)).toEqual(["trash"]);
    expect(model.suggestions.map((item) => item.id)).toEqual(["suggest", "skip"]);
    expect(model.executableCount).toBe(2);
    expect(model.needsApproval).toBe(true);
  });

  it("requires approval for backend status or risk warnings without executable items", () => {
    expect(buildCleanupPreviewModel(cleanupPlan({ status: "needs_approval" })).needsApproval).toBe(true);
    expect(buildCleanupPreviewModel(cleanupPlan({ riskWarnings: ["Review this path"] })).needsApproval).toBe(true);
    expect(buildCleanupPreviewModel(cleanupPlan()).needsApproval).toBe(false);
  });
});

describe("useFileCleanupWorkspace", () => {
  it("keeps the initial cleanup pane copy and style hooks stable", () => {
    function Harness() {
      return useFileCleanupWorkspace({
        currentScope: "C:\\Users\\Suli\\Downloads",
        hasPendingApproval: false
      }).pane;
    }

    const markup = renderToStaticMarkup(createElement(Harness));

    expect(markup).toContain('class="file-tool-pane"');
    expect(markup).toContain('aria-label="清理预览"');
    expect(markup).toContain('class="cleanup-safety-gate"');
    expect(markup).toContain("只读扫描可清理项");
    expect(markup).toContain("真正移动或删除文件前，还会让你确认");
  });
});
