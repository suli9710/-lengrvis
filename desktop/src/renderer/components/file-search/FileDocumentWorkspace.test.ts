import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FileDocumentPane } from "./FileDocumentPane";
import { DEFAULT_SUMMARY_QUESTION } from "./FileSearchModels";
import {
  compareDocumentValidationError,
  documentWorkingActionForQuestion,
  questionForDocumentIntent,
  useFileDocumentWorkspace
} from "./FileDocumentWorkspace";

describe("document workspace helpers", () => {
  it("keeps document intent defaults and working actions stable", () => {
    expect(questionForDocumentIntent("summarize")).toBe(DEFAULT_SUMMARY_QUESTION);
    expect(questionForDocumentIntent("ask")).toBe("");
    expect(questionForDocumentIntent("summarize", "请总结第 2 页")).toBe("请总结第 2 页");

    expect(documentWorkingActionForQuestion(DEFAULT_SUMMARY_QUESTION)).toBe("summarize");
    expect(documentWorkingActionForQuestion("这份合同的付款条款是什么？")).toBe("ask");
  });

  it("labels compare validation errors by the document slot that failed", () => {
    const validPath = "C:\\Users\\Suli\\Documents\\report.pdf";

    expect(compareDocumentValidationError("report.pdf", validPath)).toContain("第一份文档");
    expect(compareDocumentValidationError(validPath, "notes.tmp")).toContain("第二份文档");
    expect(compareDocumentValidationError(validPath, "C:\\Users\\Suli\\Documents\\notes.docx")).toBeNull();
  });
});

describe("useFileDocumentWorkspace", () => {
  it("provides the document pane with the initial document-workspace surface", () => {
    function Harness() {
      const workspace = useFileDocumentWorkspace({
        ensureDocumentScopes: async () => true,
        onSelectDocumentTool: () => undefined
      });

      return createElement(FileDocumentPane, {
        ...workspace.paneProps,
        results: [],
        serviceUnavailable: false,
        onSelectTool: () => undefined
      });
    }

    const markup = renderToStaticMarkup(createElement(Harness));

    expect(markup).toContain('aria-label="文档操作区"');
    expect(markup).toContain("先选择一份文档");
    expect(markup).toContain("选择并总结");
    expect(markup).toContain("当前还没有选中文档");
  });
});
