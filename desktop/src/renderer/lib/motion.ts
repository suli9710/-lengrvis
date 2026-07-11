import type { EffectiveMotion } from "./uiPreferences";

export function effectiveMotionPreference(): EffectiveMotion {
  if (typeof document !== "undefined") {
    const configured = document.documentElement.dataset.motion;
    if (configured === "full" || configured === "reduced") return configured;
  }
  return Boolean(
    typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  )
    ? "reduced"
    : "full";
}

export function prefersReducedMotion(): boolean {
  return effectiveMotionPreference() === "reduced";
}

export function motionAwareScrollBehavior(): ScrollBehavior {
  return prefersReducedMotion() ? "auto" : "smooth";
}
