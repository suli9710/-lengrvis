export interface UpdateRuntimeHealthEvidence {
  backendHealthy: boolean;
  rendererHealthy: boolean;
}

export interface UpdateHealthWindow {
  healthySince: number | null;
  ready: boolean;
}

/**
 * Advances the continuous-health window used to promote an updated build.
 * Any failed prerequisite resets the window instead of letting intermittent
 * health accumulate into a false last-good result.
 */
export function advanceUpdateHealthWindow(
  healthySince: number | null,
  evidence: UpdateRuntimeHealthEvidence,
  now: number,
  requiredStableMs: number
): UpdateHealthWindow {
  if (!evidence.backendHealthy || !evidence.rendererHealthy) {
    return { healthySince: null, ready: false };
  }

  const startedAt = healthySince ?? now;
  return {
    healthySince: startedAt,
    ready: now - startedAt >= requiredStableMs
  };
}
