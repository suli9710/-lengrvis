import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel } from "./ChatPanel";

describe("ChatPanel intent suggestions", () => {
  it("shows qualitative strength and matching reasons instead of uncalibrated percentages", () => {
    let renderer!: ReactTestRenderer;

    act(() => {
      renderer = create(
        createElement(ChatPanel, {
          messages: [],
          connectionState: "online",
          onSend: vi.fn(),
          suggestions: [
            suggestion("high", 0.95, "检测到当前窗口是报表"),
            suggestion("medium", 0.84, "检测到未完成的任务"),
            suggestion("low", 0.62, "")
          ]
        })
      );
    });

    const text = textContent(renderer);
    expect(text).toContain("建议强度：高");
    expect(text).toContain("建议强度：中");
    expect(text).toContain("建议强度：低");
    const reasons = renderer.root.findAllByType("small").map((node) => node.children.join(""));
    expect(reasons).toContain("匹配原因：检测到当前窗口是报表");
    expect(reasons).toContain("匹配原因：根据当前可见窗口与任务上下文匹配");
    expect(text).not.toMatch(/\b(?:95|84|62)%/);
  });
});

function suggestion(id: string, confidence: number, reason: string) {
  return {
    id,
    title: `建议 ${id}`,
    prompt: `执行 ${id}`,
    confidence,
    reason
  };
}

function textContent(renderer: ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}
