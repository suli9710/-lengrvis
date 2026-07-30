export const MAIN_WINDOW_DEFAULT_WIDTH = 1440;
export const MAIN_WINDOW_DEFAULT_HEIGHT = 960;
export const MAIN_WINDOW_MIN_WIDTH = 640;
export const MAIN_WINDOW_MIN_HEIGHT = 480;

const MAIN_WINDOW_WORK_AREA_MARGIN = 32;

export interface WindowWorkArea {
  width: number;
  height: number;
}

export interface WindowSize {
  width: number;
  height: number;
}

export interface WindowBounds extends WindowSize {
  x: number;
  y: number;
}

export interface MainWindowConstraints {
  bounds: WindowBounds;
  minimumSize: WindowSize;
}

export function fitMainWindowToWorkArea(workArea: WindowWorkArea): WindowSize {
  return {
    width: fitDimension(
      workArea.width,
      MAIN_WINDOW_DEFAULT_WIDTH,
      MAIN_WINDOW_MIN_WIDTH
    ),
    height: fitDimension(
      workArea.height,
      MAIN_WINDOW_DEFAULT_HEIGHT,
      MAIN_WINDOW_MIN_HEIGHT
    )
  };
}

export function minimumMainWindowSize(initialSize: WindowSize): WindowSize {
  return {
    width: Math.min(MAIN_WINDOW_MIN_WIDTH, initialSize.width),
    height: Math.min(MAIN_WINDOW_MIN_HEIGHT, initialSize.height)
  };
}

export function mainWindowConstraintsForWorkArea(
  currentBounds: WindowBounds,
  workArea: WindowBounds
): MainWindowConstraints {
  const width = constrainCurrentDimension(currentBounds.width, workArea.width, MAIN_WINDOW_MIN_WIDTH);
  const height = constrainCurrentDimension(currentBounds.height, workArea.height, MAIN_WINDOW_MIN_HEIGHT);
  return {
    bounds: {
      x: constrainWindowCoordinate(currentBounds.x, workArea.x, workArea.width, width.size),
      y: constrainWindowCoordinate(currentBounds.y, workArea.y, workArea.height, height.size),
      width: width.size,
      height: height.size
    },
    minimumSize: { width: width.minimum, height: height.minimum }
  };
}

function fitDimension(workAreaSize: number, preferredSize: number, minimumSize: number): number {
  if (!Number.isFinite(workAreaSize) || workAreaSize <= 0) {
    return preferredSize;
  }
  const availableSize = Math.max(1, Math.floor(workAreaSize) - MAIN_WINDOW_WORK_AREA_MARGIN);
  return availableSize < minimumSize ? availableSize : Math.min(preferredSize, availableSize);
}

function constrainCurrentDimension(
  currentSize: number,
  workAreaSize: number,
  normalMinimum: number
): { size: number; minimum: number } {
  if (!Number.isFinite(workAreaSize) || workAreaSize <= 0) {
    const size = validDimension(currentSize, normalMinimum);
    return { size, minimum: Math.min(normalMinimum, size) };
  }
  const availableSize = Math.max(1, Math.floor(workAreaSize) - MAIN_WINDOW_WORK_AREA_MARGIN);
  const minimum = Math.min(normalMinimum, availableSize);
  const size = Math.min(availableSize, Math.max(minimum, validDimension(currentSize, minimum)));
  return { size, minimum };
}

function validDimension(value: number, fallback: number): number {
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
}

function constrainWindowCoordinate(
  currentCoordinate: number,
  workAreaCoordinate: number,
  workAreaSize: number,
  windowSize: number
): number {
  const start = validCoordinate(workAreaCoordinate, 0);
  if (!Number.isFinite(workAreaSize) || workAreaSize <= 0) {
    return validCoordinate(currentCoordinate, start);
  }
  const end = Math.max(start, start + Math.floor(workAreaSize) - windowSize);
  return Math.min(end, Math.max(start, validCoordinate(currentCoordinate, start)));
}

function validCoordinate(value: number, fallback: number): number {
  return Number.isFinite(value) ? Math.floor(value) : fallback;
}
