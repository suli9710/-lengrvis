import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { LengrvisApiClient } from "../../lib/apiClient";
import { SESSION_DRAFT_KEYS, useSessionDraft } from "../../lib/useSessionDraft";
import { PrivacyDataPanel } from "./PrivacyDataPanel";

describe("privacy erase draft cleanup", () => {
  it.each(["success", "failure", "cancelled"])("clears session and memory drafts only on confirmed success: %s", async (outcome) => {
    const values = new Map<string, string>([["unrelated-preference", "keep"]]);
    const original = Object.getOwnPropertyDescriptor(window, "sessionStorage");
    Object.defineProperty(window, "sessionStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); }
    } });
    const eraseLocalData = vi.fn().mockResolvedValue(outcome === "success" ? {
      ok: true, data: { deletedRowsTotal: 1, deletedDiagnosticPackages: 0, settingsReset: false }
    } : { ok: false, error: { message: outcome === "cancelled" ? "用户取消了删除" : "删除失败" } });
    const api = { eraseLocalData } as unknown as LengrvisApiClient;
    let home!: ReturnType<typeof useSessionDraft>;
    let chat!: ReturnType<typeof useSessionDraft>;
    function Drafts() {
      home = useSessionDraft(SESSION_DRAFT_KEYS.home);
      chat = useSessionDraft(SESSION_DRAFT_KEYS.chat);
      return null;
    }
    let draftsRenderer!: ReactTestRenderer;
    let panel!: ReactTestRenderer;
    try {
      act(() => {
        draftsRenderer = create(createElement(Drafts));
        panel = create(createElement(PrivacyDataPanel, { api }));
      });
      act(() => { home[1]("私密首页草稿"); chat[1]("私密对话草稿"); });
      // The confirmation phrase remains required before the existing system confirmation.
      await act(async () => { panel.root.findByType("button").props.onClick(); });
      expect(eraseLocalData).not.toHaveBeenCalled();
      expect(home[0]).toBe("私密首页草稿");
      act(() => {
        panel.root.findAllByType("input").find((node) => node.props.autoComplete === "off")!
          .props.onChange({ target: { value: "删除本机数据" } });
      });
      await act(async () => { panel.root.findByType("button").props.onClick(); });
      expect(eraseLocalData).toHaveBeenCalledOnce();
      expect(home[0]).toBe(outcome === "success" ? "" : "私密首页草稿");
      expect(chat[0]).toBe(outcome === "success" ? "" : "私密对话草稿");
      expect(values.has(SESSION_DRAFT_KEYS.home)).toBe(outcome !== "success");
      expect(values.has(SESSION_DRAFT_KEYS.chat)).toBe(outcome !== "success");
      expect(values.get("unrelated-preference")).toBe("keep");
    } finally {
      act(() => { home[1](""); chat[1](""); panel?.unmount(); draftsRenderer?.unmount(); });
      if (original) Object.defineProperty(window, "sessionStorage", original);
      else Reflect.deleteProperty(window, "sessionStorage");
    }
  });
});
