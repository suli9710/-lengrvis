import { createElement, type ComponentProps, type ReactNode } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApprovalRequest } from "../../shared/executionTypes";
import { ApprovalDialog } from "./ApprovalDialog";

vi.mock("./AccessibleDialog", () => ({
  AccessibleDialog: ({ children }: { children: ReactNode }) => createElement("div", null, children)
}));

const renderers: ReactTestRenderer[] = [];
beforeEach(() => {
  vi.stubGlobal("window", { setTimeout, clearTimeout });
});
afterEach(async () => {
  for (const renderer of renderers.splice(0)) await act(async () => renderer.unmount());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function approval(): ApprovalRequest {
  return {
    id: "approval-1", title: "移动文件", reason: "需要确认", requester: "FileAgent",
    riskLevel: "high", createdAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 60_000).toISOString(), proposedAction: "移动到已授权目录", status: "pending"
  };
}

type DecisionHandler = ComponentProps<typeof ApprovalDialog>["onDecision"];

async function renderDialog(onDecision: DecisionHandler, request = approval()) {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(ApprovalDialog, { approval: request, isOpen: true, onClose: vi.fn(), onDecision }));
  });
  renderers.push(renderer);
  return renderer;
}

describe("ApprovalDialog", () => {
  it("submits once for repeated clicks and retains the approval note after a failure", async () => {
    let rejectDecision!: (error: Error) => void;
    const onDecision = vi.fn<DecisionHandler>().mockReturnValueOnce(new Promise((_resolve, reject) => { rejectDecision = reject; }))
      .mockResolvedValueOnce(undefined);
    const renderer = await renderDialog(onDecision);
    await act(async () => renderer.root.findByType("textarea").props.onChange({ target: { value: "仅移动预览中的文件" } }));
    const confirm = renderer.root.findByProps({ className: "button button--primary" });
    await act(async () => {
      confirm.props.onClick();
      confirm.props.onClick();
    });
    expect(onDecision).toHaveBeenCalledTimes(1);
    expect(renderer.root.findByType("textarea").props.disabled).toBe(true);
    expect(renderer.root.findByProps({ className: "approval-tech-details" }).props.open).toBe(true);
    await act(async () => rejectDecision(new Error("审批提交失败，请重试")));
    expect(renderer.root.findByType("textarea").props.value).toBe("仅移动预览中的文件");
    expect(JSON.stringify(renderer.toJSON())).toContain("审批提交失败，请重试");
    await act(async () => renderer.root.findByProps({ className: "button button--primary" }).props.onClick());
    expect(onDecision).toHaveBeenCalledTimes(2);
    expect(renderer.root.findByType("textarea").props.value).toBe("");
  });

  it("rechecks expiry at the moment of deciding even before the expiry timer runs", async () => {
    const request = approval();
    const onDecision = vi.fn<DecisionHandler>();
    const renderer = await renderDialog(onDecision, request);
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(request.expiresAt!) + 1);
    await act(async () => renderer.root.findByProps({ className: "button button--primary" }).props.onClick());
    expect(onDecision).not.toHaveBeenCalled();
    expect(JSON.stringify(renderer.toJSON())).toContain("此审批授权已过期");
  });
});
