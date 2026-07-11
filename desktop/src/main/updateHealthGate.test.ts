import { describe, expect, it } from "vitest";

import { advanceUpdateHealthWindow } from "./updateHealthGate";

describe("advanceUpdateHealthWindow", () => {
  it("requires continuously healthy backend and renderer evidence for the full stability window", () => {
    const duration = 60_000;

    expect(advanceUpdateHealthWindow(null, {
      backendHealthy: false,
      rendererHealthy: true
    }, 1_000, duration)).toEqual({ healthySince: null, ready: false });

    const firstHealthy = advanceUpdateHealthWindow(null, {
      backendHealthy: true,
      rendererHealthy: true
    }, 2_000, duration);
    expect(firstHealthy).toEqual({ healthySince: 2_000, ready: false });

    expect(advanceUpdateHealthWindow(firstHealthy.healthySince, {
      backendHealthy: true,
      rendererHealthy: true
    }, 61_999, duration).ready).toBe(false);
    expect(advanceUpdateHealthWindow(firstHealthy.healthySince, {
      backendHealthy: true,
      rendererHealthy: false
    }, 62_000, duration)).toEqual({ healthySince: null, ready: false });

    const restarted = advanceUpdateHealthWindow(null, {
      backendHealthy: true,
      rendererHealthy: true
    }, 63_000, duration);
    expect(advanceUpdateHealthWindow(restarted.healthySince, {
      backendHealthy: true,
      rendererHealthy: true
    }, 123_000, duration).ready).toBe(true);
  });
});
