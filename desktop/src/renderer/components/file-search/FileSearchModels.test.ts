import { describe, expect, it } from "vitest";

import {
  buildFileOnboardingSteps,
  displayFilePath,
  fileOnboardingHeadline,
  noticeForSearchStatus
} from "./FileSearchModels";

describe("file search onboarding model", () => {
  it("moves from scope selection to search and then document work", () => {
    const missingScope = buildFileOnboardingSteps({
      currentScope: "",
      activeTool: "search",
      searchStatus: "idle",
      resultsCount: 0,
      selectedDocumentPath: "",
      documentReady: false,
      cleanupReady: false
    });
    const searchReady = buildFileOnboardingSteps({
      currentScope: "C:\\Users\\Suli\\Documents",
      activeTool: "search",
      searchStatus: "success",
      resultsCount: 1,
      selectedDocumentPath: "",
      documentReady: false,
      cleanupReady: false
    });

    expect(missingScope.map((step) => step.state)).toEqual(["current", "next", "next", "next"]);
    expect(fileOnboardingHeadline(missingScope)).toBe("先给 Lengrvis 一个明确文件夹");
    expect(searchReady.map((step) => step.state)).toEqual(["done", "done", "current", "next"]);
    expect(fileOnboardingHeadline(searchReady)).toBe("选中文档后读取、总结或提问");
  });
});

describe("file search status model", () => {
  it("distinguishes incomplete scans from complete empty results", () => {
    expect(noticeForSearchStatus("empty", null, 0, { count: 0, scanned: 25, truncated: true })?.text)
      .toContain("当前范围还没完全扫完");
    expect(noticeForSearchStatus("empty", null, 0, { count: 0, scanned: 25, truncated: false })?.text)
      .toContain("没有找到匹配项");
  });

  it("keeps file name and compact parent path separate", () => {
    expect(displayFilePath("C:\\Users\\Suli\\Documents\\Reports\\summary.pdf")).toEqual({
      name: "summary.pdf",
      parent: ".../Suli/Documents/Reports"
    });
  });
});
