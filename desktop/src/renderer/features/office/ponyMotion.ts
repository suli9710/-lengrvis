import { officeViewBox, type OfficeAgentPose, type OfficeMapSize } from "./model";

export type OfficeFacing = "left" | "right";
export type OfficeTravelPhase = "idle" | "turning" | "travelling" | "settling";
export type PonyClip = "idle" | "walk" | "working" | "phone" | "coffee" | "treadmill" | "nap" | "salute" | "restroom";

export interface OfficeMotionPoint {
  x: number;
  y: number;
}

export interface OfficeTravelPlan {
  motionId: number;
  from: OfficeMotionPoint;
  target: OfficeMotionPoint;
  distance: number;
  durationMs: number;
  facing: OfficeFacing;
  needsTurn: boolean;
  phases: readonly OfficeTravelPhase[];
}

export const officeTravelTolerance = 1.5;
export const officeFacingThreshold = 8;
export const officeTurnDurationMs = 120;
export const officeSettleDurationMs = 160;
export const ponyClipEnterDurationMs = 140;
export const ponyClipSwitchDurationMs = 180;

export function officePointDistance(from: OfficeMotionPoint, target: OfficeMotionPoint) {
  return Math.hypot(target.x - from.x, target.y - from.y);
}

export function officeTravelDuration(distance: number) {
  return clamp((Math.max(0, distance) / 260) * 1000, 900, 3600);
}

export function hasOfficeTargetChanged(
  previous: OfficeMotionPoint | undefined,
  next: OfficeMotionPoint,
  tolerance = officeTravelTolerance
) {
  return !previous || officePointDistance(previous, next) > tolerance;
}

export function resolveOfficeFacing(
  current: OfficeFacing,
  from: OfficeMotionPoint,
  target: OfficeMotionPoint
): OfficeFacing {
  const dx = target.x - from.x;
  if (dx > officeFacingThreshold) return "right";
  if (dx < -officeFacingThreshold) return "left";
  return current;
}

export function createOfficeTravelPlan({
  motionId,
  from,
  target,
  facing,
  reducedMotion = false,
  durationMs
}: {
  motionId: number;
  from: OfficeMotionPoint;
  target: OfficeMotionPoint;
  facing: OfficeFacing;
  reducedMotion?: boolean;
  durationMs?: number;
}): OfficeTravelPlan | null {
  const distance = officePointDistance(from, target);
  if (reducedMotion || distance <= officeTravelTolerance) return null;

  const nextFacing = resolveOfficeFacing(facing, from, target);
  const needsTurn = nextFacing !== facing;
  return {
    motionId,
    from,
    target,
    distance,
    durationMs: durationMs ?? officeTravelDuration(distance),
    facing: nextFacing,
    needsTurn,
    phases: needsTurn
      ? ["turning", "travelling", "settling", "idle"]
      : ["travelling", "settling", "idle"]
  };
}

export function ponyClipForOfficePose(
  pose: OfficeAgentPose,
  phase: OfficeTravelPhase
): PonyClip {
  if (phase === "travelling" || phase === "settling") return "walk";
  if (phase === "turning") return "idle";
  if (pose === "review") return "salute";
  if (pose === "wander") return "idle";
  return pose;
}

export function isCurrentOfficeMotion(completedMotionId: number, currentMotionId: number) {
  return completedMotionId === currentMotionId;
}

export function remainingOfficeTravelDuration(durationMs: number, elapsedMs: number) {
  return Math.max(0, durationMs - Math.max(0, elapsedMs));
}

export function unprojectOfficePoint(point: OfficeMotionPoint, mapSize: OfficeMapSize): OfficeMotionPoint {
  if (mapSize.width <= 0 || mapSize.height <= 0) return { x: 0, y: 0 };
  const scale = Math.min(mapSize.width / officeViewBox.width, mapSize.height / officeViewBox.height);
  const offsetX = (mapSize.width - officeViewBox.width * scale) / 2;
  const offsetY = (mapSize.height - officeViewBox.height * scale) / 2;
  return {
    x: (point.x - offsetX) / scale,
    y: (point.y - offsetY) / scale
  };
}

export function officeTravelKeyframes(from: OfficeMotionPoint, target: OfficeMotionPoint): Keyframe[] {
  const at = (progress: number) =>
    `${round(from.x + (target.x - from.x) * progress)}px ${round(from.y + (target.y - from.y) * progress)}px`;
  return [
    { translate: at(0), offset: 0, easing: "cubic-bezier(0.55, 0, 0.75, 0.45)" },
    { translate: at(0.1), offset: 0.18, easing: "linear" },
    { translate: at(0.9), offset: 0.82, easing: "cubic-bezier(0.25, 0.55, 0.45, 1)" },
    { translate: at(1), offset: 1 }
  ];
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function round(value: number) {
  return Math.round(value * 100) / 100;
}
