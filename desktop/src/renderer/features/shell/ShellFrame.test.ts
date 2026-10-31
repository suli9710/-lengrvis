import { createElement } from "react";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TaskEvent } from "../../../shared/executionTypes";
import type { ConnectionState } from "../../store";

vi.mock("../../lib/uiPreferences", () => ({ useUiPreferences: () => ({ effectiveMotion: "reduced" }) }));
import { ShellFrame } from "./ShellFrame";
let renderer: ReactTestRenderer | undefined;
beforeEach(() => {
  vi.stubGlobal("window", { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  vi.stubGlobal("document", { documentElement: { style: {} }, body: { style: {} } });
});
afterEach(async () => {
  if (renderer) await act(async () => renderer?.unmount());
  renderer = undefined;
  vi.unstubAllGlobals();
});
function text(node: ReactTestInstance | string): string {
  if (typeof node === "string") return node;
  if (node.props["aria-hidden"] === true) return "";
  return node.children.map(text).join("");
}

describe("activity center label in accessible name", () => {
  it.each([
    ["online", false, undefined, "待命"],
    ["online", true, undefined, "运行中"],
    ["checking", false, undefined, "连接中"],
    ["offline", false, undefined, "待连接"],
    ["online", false, "completed", "已完成"],
    ["online", false, "failed", "需关注"]
  ] as const)("includes contiguous visible label for %s/%s/%s", async (connection, loading, state, label) => {
    const tasks: TaskEvent[] = state ? [{ id: "task", title: "Task", description: "Task", state,
      agent: "agent", createdAt: "2026-10-31T00:00:00Z", updatedAt: "2026-10-31T00:00:00Z" }] : [];
    await act(async () => {
      renderer = create(createElement(ShellFrame, {
        activeView: "home", connectionState: connection as ConnectionState, isLoading: loading,
        onViewChange: vi.fn(), onRefresh: vi.fn(), onOpenApprovals: vi.fn(),
        hasPendingApproval: true, tasks, children: null
      }));
    });
    const trigger = () => renderer!.root.findAllByType("button").find(
      (node) => node.props.className?.includes("activity-center-trigger")
    )!;
    expect(text(trigger())).toBe(`活动中心${label}`);
    expect(trigger().props["aria-label"]).toContain(text(trigger()));
    expect(trigger().props["aria-label"]).toContain("打开活动中心");
    expect(trigger().props["aria-expanded"]).toBe(false);
    await act(async () => trigger().props.onClick());
    expect(trigger().props["aria-expanded"]).toBe(true);
    expect(trigger().props["aria-label"]).toContain(text(trigger()));
    expect(trigger().props["aria-label"]).toContain("关闭活动中心");
  });
});
