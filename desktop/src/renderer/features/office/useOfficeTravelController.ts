import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { projectOfficePoint, type OfficeMapSize } from "./model";
import {
  createOfficeTravelPlan,
  hasOfficeTargetChanged,
  isCurrentOfficeMotion,
  officeSettleDurationMs,
  officeTravelKeyframes,
  officeTurnDurationMs,
  remainingOfficeTravelDuration,
  resolveOfficeFacing,
  unprojectOfficePoint,
  type OfficeFacing,
  type OfficeMotionPoint,
  type OfficeTravelPhase,
  type OfficeTravelPlan
} from "./ponyMotion";

interface OfficeTravelControllerOptions {
  target: OfficeMotionPoint;
  mapSize: OfficeMapSize;
  reducedMotion: boolean;
  paused: boolean;
}

export function useOfficeTravelController({
  target,
  mapSize,
  reducedMotion,
  paused
}: OfficeTravelControllerOptions) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [phase, setPhaseState] = useState<OfficeTravelPhase>("idle");
  const [facing, setFacingState] = useState<OfficeFacing>("right");
  const phaseRef = useRef<OfficeTravelPhase>("idle");
  const facingRef = useRef<OfficeFacing>("right");
  const previousTargetRef = useRef<OfficeMotionPoint>();
  const previousMapSizeRef = useRef<OfficeMapSize>();
  const planRef = useRef<OfficeTravelPlan | null>(null);
  const travelAnimationRef = useRef<Animation | null>(null);
  const phaseAnimationRef = useRef<Animation | null>(null);
  const motionIdRef = useRef(0);
  const pausedRef = useRef(paused);

  const setPhase = (next: OfficeTravelPhase) => {
    phaseRef.current = next;
    setPhaseState(next);
  };
  const setFacing = (next: OfficeFacing) => {
    facingRef.current = next;
    setFacingState(next);
  };
  const pauseCurrentAnimations = (shouldPause: boolean) => {
    setAnimationPaused(travelAnimationRef.current, shouldPause);
    setAnimationPaused(phaseAnimationRef.current, shouldPause);
  };
  const cancelCurrentMotion = () => {
    motionIdRef.current += 1;
    travelAnimationRef.current?.cancel();
    phaseAnimationRef.current?.cancel();
    travelAnimationRef.current = null;
    phaseAnimationRef.current = null;
  };

  const startSettling = (
    motionId: number,
    destination: OfficeMotionPoint,
    size: OfficeMapSize,
    duration = officeSettleDurationMs
  ) => {
    const element = anchorRef.current;
    if (!element || !isCurrentOfficeMotion(motionId, motionIdRef.current)) return;
    setPhase("settling");
    const projected = projectOfficePoint(destination.x, destination.y, size);
    const value = `${projected.x}px ${projected.y}px`;
    const animation = element.animate([{ translate: value }, { translate: value }], {
      duration: Math.max(1, duration),
      fill: "both"
    });
    phaseAnimationRef.current = animation;
    setAnimationPaused(animation, pausedRef.current);
    animation.finished.then(
      () => {
        if (!isCurrentOfficeMotion(motionId, motionIdRef.current)) return;
        animation.cancel();
        if (phaseAnimationRef.current === animation) phaseAnimationRef.current = null;
        planRef.current = null;
        setPhase("idle");
      },
      () => undefined
    );
  };

  const startTravelling = (
    motionId: number,
    plan: OfficeTravelPlan,
    size: OfficeMapSize
  ) => {
    const element = anchorRef.current;
    if (!element || !isCurrentOfficeMotion(motionId, motionIdRef.current)) return;
    setPhase("travelling");
    const from = projectOfficePoint(plan.from.x, plan.from.y, size);
    const destination = projectOfficePoint(plan.target.x, plan.target.y, size);
    const animation = element.animate(officeTravelKeyframes(from, destination), {
      duration: Math.max(1, plan.durationMs),
      easing: "linear",
      fill: "both"
    });
    travelAnimationRef.current = animation;
    setAnimationPaused(animation, pausedRef.current);
    animation.finished.then(
      () => {
        if (!isCurrentOfficeMotion(motionId, motionIdRef.current)) return;
        animation.cancel();
        if (travelAnimationRef.current === animation) travelAnimationRef.current = null;
        startSettling(motionId, plan.target, size);
      },
      () => undefined
    );
  };

  const startTurning = (
    motionId: number,
    plan: OfficeTravelPlan,
    size: OfficeMapSize,
    duration = officeTurnDurationMs
  ) => {
    const element = anchorRef.current;
    if (!element || !isCurrentOfficeMotion(motionId, motionIdRef.current)) return;
    setPhase("turning");
    const projected = projectOfficePoint(plan.from.x, plan.from.y, size);
    const value = `${projected.x}px ${projected.y}px`;
    const animation = element.animate([{ translate: value }, { translate: value }], {
      duration: Math.max(1, duration),
      fill: "both"
    });
    phaseAnimationRef.current = animation;
    setAnimationPaused(animation, pausedRef.current);
    animation.finished.then(
      () => {
        if (!isCurrentOfficeMotion(motionId, motionIdRef.current)) return;
        animation.cancel();
        if (phaseAnimationRef.current === animation) phaseAnimationRef.current = null;
        startTravelling(motionId, plan, size);
      },
      () => undefined
    );
  };

  useLayoutEffect(() => {
    const element = anchorRef.current;
    const previousTarget = previousTargetRef.current;
    const previousMapSize = previousMapSizeRef.current;
    const targetChanged = hasOfficeTargetChanged(previousTarget, target);
    const mapSizeChanged = Boolean(previousMapSize) && (
      Math.abs((previousMapSize?.width ?? 0) - mapSize.width) > 0.1 ||
      Math.abs((previousMapSize?.height ?? 0) - mapSize.height) > 0.1
    );

    if (!previousTarget || !previousMapSize || !element) {
      previousTargetRef.current = target;
      previousMapSizeRef.current = mapSize;
      return;
    }

    if (reducedMotion) {
      cancelCurrentMotion();
      if (targetChanged) setFacing(resolveOfficeFacing(facingRef.current, previousTarget, target));
      planRef.current = null;
      setPhase("idle");
      previousTargetRef.current = target;
      previousMapSizeRef.current = mapSize;
      return;
    }

    if (targetChanged) {
      const activeAnimation = travelAnimationRef.current ?? phaseAnimationRef.current;
      const currentScreen = activeAnimation ? readCurrentTranslate(element) : null;
      const from = currentScreen
        ? unprojectOfficePoint(currentScreen, previousMapSize)
        : previousTarget;
      cancelCurrentMotion();
      const motionId = motionIdRef.current;
      const plan = createOfficeTravelPlan({
        motionId,
        from,
        target,
        facing: facingRef.current
      });
      previousTargetRef.current = target;
      previousMapSizeRef.current = mapSize;
      if (!plan) {
        planRef.current = null;
        setPhase("idle");
        return;
      }
      planRef.current = plan;
      setFacing(plan.facing);
      if (plan.needsTurn) startTurning(motionId, plan, mapSize);
      else startTravelling(motionId, plan, mapSize);
      return;
    }

    if (mapSizeChanged && phaseRef.current !== "idle") {
      const currentScreen = readCurrentTranslate(element);
      const currentMap = currentScreen
        ? unprojectOfficePoint(currentScreen, previousMapSize)
        : previousTarget;
      const previousPlan = planRef.current;
      const activePhase = phaseRef.current;
      const remaining = animationRemaining(
        activePhase === "travelling" ? travelAnimationRef.current : phaseAnimationRef.current
      );
      cancelCurrentMotion();
      const motionId = motionIdRef.current;

      if (activePhase === "settling") {
        startSettling(motionId, target, mapSize, remaining || officeSettleDurationMs);
      } else if (previousPlan) {
        const plan = createOfficeTravelPlan({
          motionId,
          from: currentMap,
          target,
          facing: facingRef.current,
          durationMs: activePhase === "travelling"
            ? Math.max(80, remaining)
            : previousPlan.durationMs
        });
        if (plan) {
          planRef.current = plan;
          if (activePhase === "turning") startTurning(motionId, plan, mapSize, remaining || officeTurnDurationMs);
          else startTravelling(motionId, plan, mapSize);
        } else {
          planRef.current = null;
          setPhase("idle");
        }
      }
    }

    previousTargetRef.current = target;
    previousMapSizeRef.current = mapSize;
  }, [mapSize.height, mapSize.width, reducedMotion, target.x, target.y]);

  useLayoutEffect(() => {
    pausedRef.current = paused;
    pauseCurrentAnimations(paused);
  }, [paused]);

  useEffect(() => () => {
    cancelCurrentMotion();
  }, []);

  return { anchorRef, phase, facing };
}

function readCurrentTranslate(element: HTMLElement): OfficeMotionPoint | null {
  const value = getComputedStyle(element).translate;
  if (!value || value === "none") return null;
  const values = value.match(/-?\d+(?:\.\d+)?/g)?.map(Number);
  if (!values || values.length < 2 || values.some((item) => !Number.isFinite(item))) return null;
  return { x: values[0] ?? 0, y: values[1] ?? 0 };
}

function animationRemaining(animation: Animation | null) {
  if (!animation) return 0;
  const duration = animation.effect?.getTiming().duration;
  const currentTime = animation.currentTime;
  if (typeof duration !== "number" || typeof currentTime !== "number") return 0;
  return remainingOfficeTravelDuration(duration, currentTime);
}

function setAnimationPaused(animation: Animation | null, paused: boolean) {
  if (!animation) return;
  if (paused && animation.playState === "running") animation.pause();
  if (!paused && animation.playState === "paused") animation.play();
}
