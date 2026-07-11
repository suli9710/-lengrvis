import { afterEach, describe, expect, it, vi } from "vitest";

import { effectiveMotionPreference, motionAwareScrollBehavior, prefersReducedMotion } from "./motion";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("motion helpers", () => {
  it("uses the resolved root preference when present", () => {
    vi.stubGlobal("document", { documentElement: { dataset: { motion: "reduced" } } });
    vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });

    expect(effectiveMotionPreference()).toBe("reduced");
    expect(prefersReducedMotion()).toBe(true);
    expect(motionAwareScrollBehavior()).toBe("auto");
  });

  it("falls back to the operating system preference before the provider mounts", () => {
    vi.stubGlobal("document", { documentElement: { dataset: {} } });
    vi.stubGlobal("window", { matchMedia: () => ({ matches: true }) });

    expect(effectiveMotionPreference()).toBe("reduced");
  });

  it("allows an explicit full-motion preference", () => {
    vi.stubGlobal("document", { documentElement: { dataset: { motion: "full" } } });
    vi.stubGlobal("window", { matchMedia: () => ({ matches: true }) });

    expect(effectiveMotionPreference()).toBe("full");
    expect(motionAwareScrollBehavior()).toBe("smooth");
  });
});
