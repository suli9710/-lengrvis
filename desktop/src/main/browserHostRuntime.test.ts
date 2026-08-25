import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  BrowserView: class BrowserView {},
  WebContentsView: class WebContentsView {}
}));

import { BROWSER_ACTION_MAX_DELAY_MS, delay } from "./browserHostRuntime";

afterEach(() => {
  vi.useRealTimers();
});

describe("browserHostRuntime delay", () => {
  it.each([-1, BROWSER_ACTION_MAX_DELAY_MS + 1, Number.NaN, Number.POSITIVE_INFINITY, 1.5])(
    "rejects an unsafe delay without allocating a timer: %s",
    async (value) => {
      vi.useFakeTimers();

      await expect(delay(value)).rejects.toThrow(RangeError);
      expect(vi.getTimerCount()).toBe(0);
    }
  );

  it("allows a bounded integer delay", async () => {
    vi.useFakeTimers();

    const pending = delay(BROWSER_ACTION_MAX_DELAY_MS);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(BROWSER_ACTION_MAX_DELAY_MS);

    await expect(pending).resolves.toBeUndefined();
  });
});
