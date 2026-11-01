import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it } from "vitest";

import { useSessionDraft } from "./useSessionDraft";

describe("session drafts", () => {
  it.each([false, true])("retains navigation drafts when storage is blocked: %s", (blocked) => {
    const values = new Map<string, string>();
    const key = `test-draft-${blocked}`;
    values.set(key, "刷新前的草稿");
    const original = Object.getOwnPropertyDescriptor(window, "sessionStorage");
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      get: () => {
        if (blocked) throw new Error("Storage blocked");
        return {
          getItem: (name: string) => values.get(name) ?? null,
          setItem: (name: string, value: string) => { values.set(name, value); },
          removeItem: (name: string) => { values.delete(name); }
        };
      }
    });
    let current!: ReturnType<typeof useSessionDraft>;
    let renderer!: ReactTestRenderer;
    function Harness() {
      current = useSessionDraft(key);
      return null;
    }
    try {
      act(() => { renderer = create(createElement(Harness)); });
      expect(current[0]).toBe(blocked ? "" : "刷新前的草稿");
      act(() => { current[1]("新的草稿"); });
      if (!blocked) expect(values.get(key)).toBe("新的草稿");
      act(() => { renderer.unmount(); });
      act(() => { renderer = create(createElement(Harness)); });
      expect(current[0]).toBe("新的草稿");
      act(() => { current[1](""); });
      if (!blocked) expect(values.has(key)).toBe(false);
    } finally {
      act(() => { renderer?.unmount(); });
      if (original) Object.defineProperty(window, "sessionStorage", original);
      else Reflect.deleteProperty(window, "sessionStorage");
    }
  });
});
