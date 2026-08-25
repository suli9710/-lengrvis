const SESSION_REFRESH_LEAD_MS = 5 * 60 * 1000;
const SESSION_REFRESH_RETRY_BASE_MS = 60_000;
const SESSION_REFRESH_RETRY_MAX_MS = 15 * 60_000;

export type MobileSessionTransition = "lock" | "unlock" | "none";

export function mobileSessionTransition(
  previousState: string | null | undefined,
  nextState: string,
): MobileSessionTransition {
  if (nextState === "active") {
    return previousState === "active" ? "none" : "unlock";
  }
  return previousState === "active" ? "lock" : "none";
}

export function sessionRefreshDelayMs(
  session: { token?: string; expiresAt?: string },
  nowMs = Date.now(),
): number | null {
  if (!session.token?.trim() || !session.expiresAt) return null;
  const expiresAtMs = Date.parse(session.expiresAt);
  if (!Number.isFinite(expiresAtMs) || expiresAtMs <= nowMs) return null;
  return Math.max(0, expiresAtMs - nowMs - SESSION_REFRESH_LEAD_MS);
}

export function sessionRefreshRetryDelayMs(failureCount: number): number {
  const normalizedCount = Math.max(0, Math.floor(failureCount));
  return Math.min(
    SESSION_REFRESH_RETRY_MAX_MS,
    SESSION_REFRESH_RETRY_BASE_MS * 2 ** normalizedCount,
  );
}
