import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import { RuntimeControlSection } from "./AdvancedRuntimeControlSection";

describe("RuntimeControlSection async recovery", () => {
  it("disables both controls while starting and recovers with a sanitized error", async () => {
    let rejectStart!: (reason?: unknown) => void;
    const startRequest = new Promise<void>((_resolve, reject) => {
      rejectStart = reject;
    });
    const onStartBackend = vi.fn(() => startRequest);
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(RuntimeControlSection, {
        onStartBackend,
        onStopBackend: vi.fn().mockResolvedValue(undefined)
      }));
    });

    await act(async () => {
      renderer.root.findAllByType("button")[0].props.onClick();
      await Promise.resolve();
    });

    expect(renderer.root.findAllByType("button").every((button) => button.props.disabled === true)).toBe(true);
    expect(textContent(renderer)).toContain("启动中");

    await act(async () => {
      rejectStart(new Error("Error invoking backend:start at C:\\private\\service.ts"));
      await startRequest.catch(() => undefined);
      await Promise.resolve();
    });

    expect(textContent(renderer)).toContain("启动服务失败，请稍后重试。");
    expect(textContent(renderer)).not.toContain("C:\\private\\service.ts");
    expect(renderer.root.findAllByType("button").every((button) => button.props.disabled === false)).toBe(true);
  });

  it("recovers from a rejected stop request with a sanitized error", async () => {
    const onStopBackend = vi.fn().mockRejectedValue(new Error("sensitive stop IPC detail"));
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(RuntimeControlSection, {
        onStartBackend: vi.fn().mockResolvedValue(undefined),
        onStopBackend
      }));
    });

    await act(async () => {
      renderer.root.findAllByType("button")[1].props.onClick();
      await Promise.resolve();
    });

    expect(textContent(renderer)).toContain("停止服务失败，请稍后重试。");
    expect(textContent(renderer)).not.toContain("sensitive stop IPC detail");
    expect(renderer.root.findAllByType("button").every((button) => button.props.disabled === false)).toBe(true);
  });
});

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((node) => node.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
