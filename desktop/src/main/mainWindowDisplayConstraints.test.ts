import type { BrowserWindow, Display, Rectangle, Screen } from "electron";
import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";

import {
  applyMainWindowDisplayConstraints,
  registerMainWindowConstraintListeners,
  registerMainWindowDisplayConstraintListeners
} from "./mainWindowDisplayConstraints";

const COMPACT_WORK_AREA: Rectangle = { x: 0, y: 0, width: 500, height: 400 };

describe("main window display constraint wiring", () => {
  it("refits off-screen bounds after every display topology event and unregisters once", () => {
    const window = new FakeWindow({ x: 1400, y: 900, width: 1440, height: 960 });
    const displaySource = new FakeScreen(COMPACT_WORK_AREA);

    const dispose = registerMainWindowDisplayConstraintListeners(
      displaySource as unknown as Screen,
      () => window as unknown as BrowserWindow
    );

    for (const eventName of ["display-metrics-changed", "display-added", "display-removed"]) {
      window.bounds = { x: 1400, y: 900, width: 1440, height: 960 };
      displaySource.emit(eventName);
      expect(window.bounds).toEqual({ x: 32, y: 32, width: 468, height: 368 });
    }
    expect(window.setMinimumSize).toHaveBeenLastCalledWith(468, 368);
    expect(window.setMinimumSize.mock.invocationCallOrder[0]).toBeLessThan(
      window.setBounds.mock.invocationCallOrder[0]
    );

    dispose();
    dispose();
    expect(displaySource.listenerCount("display-metrics-changed")).toBe(0);
    expect(displaySource.listenerCount("display-added")).toBe(0);
    expect(displaySource.listenerCount("display-removed")).toBe(0);
  });

  it("resizes after a move without snapping position, then restores visible bounds", () => {
    const window = new FakeWindow({ x: 300, y: 250, width: 1440, height: 960 });
    const displaySource = new FakeScreen(COMPACT_WORK_AREA);
    const dispose = registerMainWindowConstraintListeners(
      window as unknown as BrowserWindow,
      displaySource
    );

    window.emit("move");
    expect(window.bounds).toEqual({ x: 300, y: 250, width: 468, height: 368 });

    for (const eventName of ["restore", "unmaximize", "leave-full-screen"]) {
      window.bounds = { x: 300, y: 250, width: 468, height: 368 };
      window.emit(eventName);
      expect(window.bounds).toEqual({ x: 32, y: 32, width: 468, height: 368 });
    }

    dispose();
    dispose();
    for (const eventName of ["move", "restore", "unmaximize", "leave-full-screen"]) {
      expect(window.listenerCount(eventName)).toBe(0);
    }
  });

  it("updates minimums but preserves native bounds while maximized", () => {
    const window = new FakeWindow({ x: 1400, y: 900, width: 1440, height: 960 });
    window.maximized = true;
    const displaySource = new FakeScreen(COMPACT_WORK_AREA);

    applyMainWindowDisplayConstraints(
      window as unknown as BrowserWindow,
      displaySource
    );

    expect(window.setMinimumSize).toHaveBeenCalledWith(468, 368);
    expect(window.setBounds).not.toHaveBeenCalled();
  });

  it("preserves native bounds in full screen and refits after leaving full screen", () => {
    const window = new FakeWindow({ x: 1400, y: 900, width: 1440, height: 960 });
    window.fullScreen = true;
    const displaySource = new FakeScreen(COMPACT_WORK_AREA);
    const dispose = registerMainWindowConstraintListeners(
      window as unknown as BrowserWindow,
      displaySource
    );

    applyMainWindowDisplayConstraints(window as unknown as BrowserWindow, displaySource);
    expect(window.setBounds).not.toHaveBeenCalled();

    window.fullScreen = false;
    window.emit("leave-full-screen");
    expect(window.bounds).toEqual({ x: 32, y: 32, width: 468, height: 368 });
    dispose();
  });

  it("ignores display events when no live main window exists", () => {
    const displaySource = new FakeScreen(COMPACT_WORK_AREA);
    const dispose = registerMainWindowDisplayConstraintListeners(
      displaySource as unknown as Screen,
      () => null
    );

    displaySource.emit("display-removed");
    expect(displaySource.getDisplayMatching).not.toHaveBeenCalled();
    dispose();
  });
});

class FakeWindow extends EventEmitter {
  bounds: Rectangle;
  maximized = false;
  fullScreen = false;
  destroyed = false;
  readonly setMinimumSize = vi.fn();
  readonly setBounds = vi.fn((bounds: Rectangle) => {
    this.bounds = { ...bounds };
  });

  constructor(bounds: Rectangle) {
    super();
    this.bounds = { ...bounds };
  }

  getBounds(): Rectangle {
    return { ...this.bounds };
  }

  isDestroyed(): boolean {
    return this.destroyed;
  }

  isMaximized(): boolean {
    return this.maximized;
  }

  isFullScreen(): boolean {
    return this.fullScreen;
  }
}

class FakeScreen extends EventEmitter {
  readonly getDisplayMatching = vi.fn(() => ({ workArea: this.workArea }) as Display);

  constructor(private readonly workArea: Rectangle) {
    super();
  }
}
