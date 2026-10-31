import { describe, expect, it } from "vitest";
import { zhBackendText } from "./zh";

describe("backend error normalization before exact translation", () => {
  it.each([
  [
    "Primary provider failed; using MockProvider fallback:",
    "当前 AI 服务临时不可用，我已切到临时模式，结果可能不完整。请稍后重试，或在设置里检查 AI 服务。"
  ],
  [
    "Provider returned invalid plan; using MockProvider fallback:",
    "当前 AI 服务临时不可用，我已切到临时模式，结果可能不完整。请稍后重试，或在设置里检查 AI 服务。"
  ],
  [
    "File paths must stay inside authorized directories; modifying steps need dry-run previews.",
    "为了保护你的文件，只会处理你先选择过的文件夹；移动、重命名或删除前会先给你确认。"
  ],
  [
    "Explicit absolute path is required when no authorized directories are configured.",
    "请先选择要整理的文件夹，或填写完整的文件位置，例如桌面、下载、文档、图片里的某个文件。"
  ],
  [
    "No authorized directories configured.",
    "还没有选择要整理的文件夹。请先在设置里添加桌面、下载、文档或图片；在你确认前，我只会查看文件。"
  ],
  [
    "Path is outside authorized directories.",
    "这个文件不在你已选择的文件夹里。请换一个文件，或先把所在文件夹加入设置。"
  ]
])("preserves normalized output for %s", (input, expected) => {
    expect(zhBackendText(input)).toBe(expected);
    expect(zhBackendText(`  ${input}  `)).toBe(expected);
    expect(zhBackendText(input.toUpperCase())).toBe(expected);
  });
  it("keeps exact translation and unknown text fallbacks", () => {
    expect(zhBackendText("Analyze spreadsheet")).toBe("分析表格");
    expect(zhBackendText("Custom backend event")).toBe("Custom backend event");
    expect(zhBackendText()).toBe("");
  });
});
