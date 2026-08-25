import { createElement, lazy, Suspense } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RendererErrorBoundary } from "./RendererErrorBoundary";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RendererErrorBoundary", () => {
  it("keeps a renderer failure recoverable without exposing the error", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const onReload = vi.fn();
    let renderer!: ReactTestRenderer;

    act(() => {
      renderer = create(
        createElement(
          RendererErrorBoundary,
          { onReload, children: createElement(BrokenRenderer) }
        )
      );
    });

    expect(textContent(renderer)).toContain("界面资源加载失败");
    expect(textContent(renderer)).not.toContain("sensitive chunk detail");
    act(() => {
      renderer.root.findByProps({ type: "button" }).props.onClick();
    });
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("recovers when a lazy renderer chunk rejects after suspending", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    let rejectChunk!: (error: Error) => void;
    const chunk = new Promise<{ default: () => null }>((_resolve, reject) => {
      rejectChunk = reject;
    });
    const LazyChunk = lazy(() => chunk);
    let renderer!: ReactTestRenderer;

    act(() => {
      renderer = create(
        createElement(
          RendererErrorBoundary,
          { children: createElement(Suspense, { fallback: createElement("p", null, "正在载入") }, createElement(LazyChunk)) }
        )
      );
    });
    expect(textContent(renderer)).toContain("正在载入");

    await act(async () => {
      rejectChunk(new Error("sensitive lazy chunk detail"));
      await chunk.catch(() => undefined);
    });

    expect(renderer.root.findByProps({ role: "alert" })).toBeTruthy();
    expect(textContent(renderer)).toContain("界面资源加载失败");
    expect(textContent(renderer)).not.toContain("sensitive lazy chunk detail");
  });

  it("renders healthy children unchanged", () => {
    const renderer = create(
      createElement(RendererErrorBoundary, {
        children: createElement("p", null, "正常界面")
      })
    );

    expect(textContent(renderer)).toContain("正常界面");
    expect(textContent(renderer)).not.toContain("界面资源加载失败");
  });
});

function BrokenRenderer(): never {
  throw new Error("sensitive chunk detail");
}

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((node) => node.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
