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

describe("ChatPanel submission", () => {
  it.each([true, false])("keeps newer input when the pending send settles (success: %s)", async (ok) => {
    Object.assign(window, { setTimeout: globalThis.setTimeout });
    let resolve!: (result: { ok: boolean; error?: string }) => void;
    const onSend = vi.fn(() => new Promise<{ ok: boolean; error?: string }>((done) => { resolve = done; }));
    let renderer!: ReactTestRenderer;
    act(() => {
      renderer = create(createElement(ChatPanel, { messages: [], connectionState: "online", onSend }));
    });
    const input = () => renderer.root.findByType("textarea");
    act(() => { input().props.onChange({ target: { value: "第一次任务" } }); });
    const send = renderer.root.findAllByType("button").find((node) => node.props.className.includes("composer__send"))!;
    act(() => {
      send.props.onClick();
      send.props.onClick();
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(input().props.value).toBe("第一次任务");
    act(() => { input().props.onChange({ target: { value: "等待期间写的新任务" } }); });
    await act(async () => { resolve({ ok, error: ok ? undefined : "连接中断" }); });
    expect(input().props.value).toBe("等待期间写的新任务");
    if (!ok) expect(textContent(renderer)).toContain("连接中断");
    act(() => { input().props.onChange({ target: { value: "" } }); renderer.unmount(); });
  });

  it("restores an unsent draft after returning and clears it only after a successful send", async () => {
    Object.assign(window, { setTimeout: globalThis.setTimeout });
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    let renderer!: ReactTestRenderer;
    const mount = () => {
      renderer = create(createElement(ChatPanel, { messages: [], connectionState: "online", onSend }));
    };
    act(mount);
    act(() => { renderer.root.findByType("textarea").props.onChange({ target: { value: "保留中文草稿" } }); });
    act(() => { renderer.unmount(); });
    act(mount);
    expect(renderer.root.findByType("textarea").props.value).toBe("保留中文草稿");
    const input = renderer.root.findByType("textarea");
    act(() => {
      input.props.onKeyDown({ key: "Enter", ctrlKey: true, nativeEvent: { isComposing: true }, preventDefault: vi.fn() });
    });
    expect(onSend).not.toHaveBeenCalled();
    await act(async () => {
      input.props.onKeyDown({ key: "Enter", ctrlKey: true, nativeEvent: { isComposing: false }, preventDefault: vi.fn() });
    });
    expect(onSend).toHaveBeenCalledOnce();
    expect(renderer.root.findByType("textarea").props.value).toBe("");
    act(() => { renderer.unmount(); });
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
