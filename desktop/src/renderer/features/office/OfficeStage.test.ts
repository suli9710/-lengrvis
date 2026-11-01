import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { OfficeAgent, getFriendlyAgentCopy, resolveWorkstationScreen } from "./OfficeStage";

import { officeAgents } from "./model";
vi.mock("../../lib/uiPreferences", () => ({ useUiPreferences: () => ({ effectiveMotion: "reduced" }) }));
vi.mock("./useOfficeTravelController", () => ({ useOfficeTravelController: () => ({ anchorRef: { current: null }, phase: "idle", facing: "front" }) }));

describe("office workstation textures", () => {
  it("uses the visible activity, name and role as the agent button's native accessible name", () => {
    const agent = officeAgents[0];
    const helper = getFriendlyAgentCopy(agent);
    const html = renderToStaticMarkup(createElement(OfficeAgent, {
      agent, mapSize: { width: 1000, height: 700 }, agentScale: 1,
      isWorking: false, isPrimary: true, motionVisible: true,
      feedback: "approval", onSelect: vi.fn()
    }));
    expect(html).not.toContain("aria-label=");
    expect(html).toContain("等你确认");
    expect(html).toContain(helper.name);
    expect(html).toContain(helper.role);
  });
  it("uses the animated screen only while visible full motion is allowed", () => {
    const animated = resolveWorkstationScreen("file", false, true, true);
    const still = resolveWorkstationScreen("file", false, true, false);

    expect(animated.src).toMatch(/\.gif(?:\?|$)/);
    expect(animated.fallbackSrc).toBe(still.src);
    expect(still.src).toMatch(/_still\.png(?:\?|$)/);
  });

  it("keeps idle screens static", () => {
    expect(resolveWorkstationScreen("file", false, false, true).src).toMatch(/screen_img\.png(?:\?|$)/);
    expect(resolveWorkstationScreen("pm", true, false, true).src).toMatch(/screen_on\.png(?:\?|$)/);
  });

  it("falls back to the static on-state for an unknown active screen", () => {
    const screen = resolveWorkstationScreen("unknown", false, true, true);
    expect(screen.src).toBe(screen.fallbackSrc);
    expect(screen.src).toMatch(/screen_on\.png(?:\?|$)/);
  });
});
