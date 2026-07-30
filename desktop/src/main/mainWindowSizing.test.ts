import { describe, expect, it } from "vitest";

import {
  fitMainWindowToWorkArea,
  MAIN_WINDOW_DEFAULT_HEIGHT,
  MAIN_WINDOW_DEFAULT_WIDTH,
  MAIN_WINDOW_MIN_HEIGHT,
  MAIN_WINDOW_MIN_WIDTH,
  mainWindowConstraintsForWorkArea,
  minimumMainWindowSize
} from "./mainWindowSizing";

describe("fitMainWindowToWorkArea", () => {
  it("keeps the preferred size on a large display", () => {
    expect(fitMainWindowToWorkArea({ width: 1920, height: 1080 })).toEqual({
      width: MAIN_WINDOW_DEFAULT_WIDTH,
      height: MAIN_WINDOW_DEFAULT_HEIGHT
    });
  });

  it("fits inside compact work areas while preserving a taskbar margin", () => {
    expect(fitMainWindowToWorkArea({ width: 1024, height: 600 })).toEqual({
      width: 992,
      height: 568
    });
  });

  it("fits inside a work area smaller than the preferred responsive minimum", () => {
    const initialSize = fitMainWindowToWorkArea({ width: 500, height: 400 });

    expect(initialSize).toEqual({ width: 468, height: 368 });
    expect(minimumMainWindowSize(initialSize)).toEqual(initialSize);
  });

  it("keeps the normal renderer minimum when the fitted window can support it", () => {
    expect(minimumMainWindowSize({ width: 992, height: 568 })).toEqual({
      width: MAIN_WINDOW_MIN_WIDTH,
      height: MAIN_WINDOW_MIN_HEIGHT
    });
  });
});

describe("mainWindowConstraintsForWorkArea", () => {
  it("lowers both the window and its minimum after an RDP work area shrink", () => {
    expect(mainWindowConstraintsForWorkArea(
      { x: 1400, y: 900, width: MAIN_WINDOW_DEFAULT_WIDTH, height: MAIN_WINDOW_DEFAULT_HEIGHT },
      { x: 0, y: 0, width: 500, height: 400 }
    )).toEqual({
      bounds: { x: 32, y: 32, width: 468, height: 368 },
      minimumSize: { width: 468, height: 368 }
    });
  });

  it("restores normal minimums without unnecessarily enlarging a valid window", () => {
    expect(mainWindowConstraintsForWorkArea(
      { x: 100, y: 80, width: 992, height: 568 },
      { x: 0, y: 0, width: 1920, height: 1080 }
    )).toEqual({
      bounds: { x: 100, y: 80, width: 992, height: 568 },
      minimumSize: { width: MAIN_WINDOW_MIN_WIDTH, height: MAIN_WINDOW_MIN_HEIGHT }
    });
  });

  it("raises a tiny prior RDP window to the normal minimum on a larger display", () => {
    expect(mainWindowConstraintsForWorkArea(
      { x: 100, y: 80, width: 468, height: 368 },
      { x: 0, y: 0, width: 1920, height: 1080 }
    )).toEqual({
      bounds: { x: 100, y: 80, width: MAIN_WINDOW_MIN_WIDTH, height: MAIN_WINDOW_MIN_HEIGHT },
      minimumSize: { width: MAIN_WINDOW_MIN_WIDTH, height: MAIN_WINDOW_MIN_HEIGHT }
    });
  });

  it("clamps a window into a work area on a display left of the primary display", () => {
    expect(mainWindowConstraintsForWorkArea(
      { x: 100, y: 20, width: 900, height: 700 },
      { x: -1280, y: 0, width: 1280, height: 1024 }
    )).toEqual({
      bounds: { x: -900, y: 20, width: 900, height: 700 },
      minimumSize: { width: MAIN_WINDOW_MIN_WIDTH, height: MAIN_WINDOW_MIN_HEIGHT }
    });
  });
});
