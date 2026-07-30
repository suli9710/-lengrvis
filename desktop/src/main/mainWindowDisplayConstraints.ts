import type { BrowserWindow, Screen } from "electron";

import { mainWindowConstraintsForWorkArea } from "./mainWindowSizing";

interface ApplyMainWindowDisplayConstraintOptions {
  clampPosition?: boolean;
}

export function applyMainWindowDisplayConstraints(
  window: BrowserWindow,
  displaySource: Pick<Screen, "getDisplayMatching">,
  options: ApplyMainWindowDisplayConstraintOptions = {}
): void {
  if (window.isDestroyed()) return;

  const currentBounds = window.getBounds();
  const display = displaySource.getDisplayMatching(currentBounds);
  const constraints = mainWindowConstraintsForWorkArea(currentBounds, display.workArea);
  window.setMinimumSize(constraints.minimumSize.width, constraints.minimumSize.height);

  if (window.isMaximized() || window.isFullScreen()) return;
  const targetBounds = options.clampPosition === false
    ? { ...constraints.bounds, x: currentBounds.x, y: currentBounds.y }
    : constraints.bounds;
  if (!sameBounds(currentBounds, targetBounds)) {
    window.setBounds(targetBounds, false);
  }
}

export function registerMainWindowConstraintListeners(
  window: BrowserWindow,
  displaySource: Pick<Screen, "getDisplayMatching">
): () => void {
  const refitSizeAfterMove = () => {
    applyMainWindowDisplayConstraints(window, displaySource, { clampPosition: false });
  };
  const refitVisibleBounds = () => {
    applyMainWindowDisplayConstraints(window, displaySource);
  };

  window.on("move", refitSizeAfterMove);
  window.on("restore", refitVisibleBounds);
  window.on("unmaximize", refitVisibleBounds);
  window.on("leave-full-screen", refitVisibleBounds);

  return once(() => {
    window.off("move", refitSizeAfterMove);
    window.off("restore", refitVisibleBounds);
    window.off("unmaximize", refitVisibleBounds);
    window.off("leave-full-screen", refitVisibleBounds);
  });
}

export function registerMainWindowDisplayConstraintListeners(
  displaySource: Screen,
  getWindow: () => BrowserWindow | null
): () => void {
  const refitVisibleBounds = () => {
    const window = getWindow();
    if (!window) return;
    applyMainWindowDisplayConstraints(window, displaySource);
  };

  displaySource.on("display-metrics-changed", refitVisibleBounds);
  displaySource.on("display-added", refitVisibleBounds);
  displaySource.on("display-removed", refitVisibleBounds);

  return once(() => {
    displaySource.off("display-metrics-changed", refitVisibleBounds);
    displaySource.off("display-added", refitVisibleBounds);
    displaySource.off("display-removed", refitVisibleBounds);
  });
}

function sameBounds(
  left: { x: number; y: number; width: number; height: number },
  right: { x: number; y: number; width: number; height: number }
): boolean {
  return left.x === right.x
    && left.y === right.y
    && left.width === right.width
    && left.height === right.height;
}

function once(dispose: () => void): () => void {
  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    dispose();
  };
}
