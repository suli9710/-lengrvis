import { createElement, type ReactNode } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../components/AccessibleDialog", async () => {
  const { createElement: createMockElement } = await import("react");
  return {
    AccessibleDialog: ({
      children,
      describedBy,
      labelledBy,
      role = "dialog"
    }: {
      children: ReactNode;
      describedBy?: string;
      labelledBy: string;
      role?: string;
    }) => createMockElement("div", {
      "aria-describedby": describedBy,
      "aria-labelledby": labelledBy,
      role
    }, children)
  };
});

import {
  ApprovalLoadFailure,
  ApprovalLoadState,
  RouteLoadFailure,
  SurfaceErrorBoundary
} from "./SurfaceErrorBoundary";

describe("SurfaceErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps a failed lazy surface inside a recoverable fallback", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const onReload = vi.fn();
    let renderer!: ReactTestRenderer;

    act(() => {
      renderer = create(createElement(
        SurfaceErrorBoundary,
        {
          children: createElement(BrokenSurface),
          fallback: createElement(RouteLoadFailure, { onReload })
        }
      ));
    });

    expect(renderer.root.findByProps({ role: "alert" })).toBeTruthy();
    expect(textContent(renderer)).toContain("此页面没有载入成功");
    act(() => renderer.root.findByType("button").props.onClick());
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("shows approval loading and a closable, reloadable failure", () => {
    let loading!: ReactTestRenderer;
    act(() => {
      loading = create(createElement(ApprovalLoadState));
    });
    expect(loading.root.findByProps({ role: "status" })).toBeTruthy();
    expect(textContent(loading)).toContain("正在载入审批");

    const onClose = vi.fn();
    const onReload = vi.fn();
    let failure!: ReactTestRenderer;
    act(() => {
      failure = create(createElement(ApprovalLoadFailure, { onClose, onReload }));
    });
    expect(failure.root.findByProps({ role: "alertdialog" })).toBeTruthy();
    const buttons = failure.root.findAllByType("button");
    expect(buttons).toHaveLength(2);
    act(() => buttons[0].props.onClick());
    act(() => buttons[1].props.onClick());
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onReload).toHaveBeenCalledTimes(1);
  });
});

function BrokenSurface(): never {
  throw new Error("chunk failed");
}

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root.findAllByType("span").map((node) => node.children.join(" ")).join(" ")
    + renderer.root.findAllByType("strong").map((node) => node.children.join(" ")).join(" ")
    + renderer.root.findAllByType("p").map((node) => node.children.join(" ")).join(" ");
}
