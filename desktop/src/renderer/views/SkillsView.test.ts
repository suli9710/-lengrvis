import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { LengrvisApiClient } from "../lib/apiClient";
import { SkillsView } from "./SkillsView";

describe("SkillsView async recovery", () => {
  it("renders a rejected catalog load and leaves refresh controls usable", async () => {
    const api = {
      listSkills: vi.fn().mockRejectedValue(new Error("技能目录 IPC 已断开"))
    } as unknown as LengrvisApiClient;
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(SkillsView, { api }));
    });

    expect(textContent(renderer)).toContain("技能目录 IPC 已断开");
    expect(textContent(renderer)).not.toContain("正在加载技能");
    expect(renderer.root.findAllByType("button")[0].props.disabled).toBe(false);
  });
});

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((node) => node.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
