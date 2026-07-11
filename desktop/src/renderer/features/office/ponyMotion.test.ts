import { describe, expect, it } from "vitest";

import { projectOfficePoint, type OfficeAgentPose } from "./model";
import {
  createOfficeTravelPlan,
  hasOfficeTargetChanged,
  isCurrentOfficeMotion,
  officeTravelDuration,
  ponyClipForOfficePose,
  remainingOfficeTravelDuration,
  resolveOfficeFacing,
  unprojectOfficePoint,
  type OfficeTravelPhase,
  type PonyClip
} from "./ponyMotion";

describe("pony office motion", () => {
  it("clamps travel duration while preserving the normal distance rate", () => {
    expect(officeTravelDuration(10)).toBe(900);
    expect(officeTravelDuration(260)).toBe(1000);
    expect(officeTravelDuration(1200)).toBe(3600);
  });

  it("changes facing only for meaningful horizontal travel", () => {
    expect(resolveOfficeFacing("left", { x: 10, y: 10 }, { x: 40, y: 12 })).toBe("right");
    expect(resolveOfficeFacing("right", { x: 40, y: 10 }, { x: 10, y: 12 })).toBe("left");
    expect(resolveOfficeFacing("left", { x: 10, y: 10 }, { x: 17, y: 80 })).toBe("left");
  });

  it("ignores movement inside the ground-anchor tolerance", () => {
    expect(hasOfficeTargetChanged({ x: 20, y: 20 }, { x: 21, y: 21 })).toBe(false);
    expect(createOfficeTravelPlan({
      motionId: 1,
      from: { x: 20, y: 20 },
      target: { x: 21, y: 21 },
      facing: "right"
    })).toBeNull();
  });

  it("maps runtime poses and movement phases to skeletal clips", () => {
    const idleMappings: Array<[OfficeAgentPose, PonyClip]> = [
      ["working", "working"],
      ["phone", "phone"],
      ["coffee", "coffee"],
      ["treadmill", "treadmill"],
      ["restroom", "restroom"],
      ["nap", "nap"],
      ["review", "salute"],
      ["wander", "idle"]
    ];
    for (const [pose, clip] of idleMappings) expect(ponyClipForOfficePose(pose, "idle")).toBe(clip);
    expect(ponyClipForOfficePose("wander", "idle")).toBe("idle");
    expect(ponyClipForOfficePose("working", "idle")).toBe("working");
    expect(ponyClipForOfficePose("coffee", "travelling")).toBe("walk");
    expect(ponyClipForOfficePose("working", "settling")).toBe("walk");
  });

  it("plans turn, travel, settle and idle in order", () => {
    const turning = createOfficeTravelPlan({
      motionId: 8,
      from: { x: 300, y: 100 },
      target: { x: 100, y: 100 },
      facing: "right"
    });
    expect(turning?.phases).toEqual(["turning", "travelling", "settling", "idle"] satisfies OfficeTravelPhase[]);

    const straight = createOfficeTravelPlan({
      motionId: 9,
      from: { x: 100, y: 100 },
      target: { x: 300, y: 100 },
      facing: "right"
    });
    expect(straight?.phases).toEqual(["travelling", "settling", "idle"] satisfies OfficeTravelPhase[]);
  });

  it("invalidates stale completion callbacks after a retarget", () => {
    expect(isCurrentOfficeMotion(11, 12)).toBe(false);
    expect(isCurrentOfficeMotion(12, 12)).toBe(true);
    expect(remainingOfficeTravelDuration(1800, 650)).toBe(1150);
  });

  it("reprojects the same map point after resize without changing its target", () => {
    const point = { x: 608, y: 277 };
    const firstSize = { width: 544, height: 448 };
    const nextSize = { width: 816, height: 672 };
    const firstProjection = projectOfficePoint(point.x, point.y, firstSize);
    const recovered = unprojectOfficePoint(firstProjection, firstSize);
    const nextProjection = projectOfficePoint(recovered.x, recovered.y, nextSize);

    expect(recovered.x).toBeCloseTo(point.x, 6);
    expect(recovered.y).toBeCloseTo(point.y, 6);
    expect(nextProjection).toEqual(projectOfficePoint(point.x, point.y, nextSize));
    expect(hasOfficeTargetChanged(point, recovered)).toBe(false);
  });

  it("returns a static target plan for reduced motion", () => {
    expect(createOfficeTravelPlan({
      motionId: 3,
      from: { x: 10, y: 10 },
      target: { x: 500, y: 500 },
      facing: "left",
      reducedMotion: true
    })).toBeNull();
    expect(ponyClipForOfficePose("coffee", "idle")).toBe("coffee");
  });
});
