import { describe, expect, it } from "vitest";

import { resolveWorkstationScreen } from "./OfficeStage";

describe("office workstation textures", () => {
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
