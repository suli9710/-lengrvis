import { refreshMobileSession, type PairingSession } from "./api/client";
import {
  clearSession as clearStoredSession,
  loadSession as loadStoredSession,
  replaceSessionIfTokenMatches,
  saveSession as saveStoredSession,
} from "./store/auth";

let sessionGeneration = 0;
type SessionRefresh = {
  generation: number;
  session: PairingSession;
  promise: Promise<PairingSession | null>;
};
const refreshesInFlight = new Map<string, SessionRefresh>();

export class SessionChangedError extends Error {
  constructor() {
    super("Mobile session changed while restoring credentials.");
    this.name = "SessionChangedError";
  }
}

/** Read authenticated storage on every unlock, sharing any unfinished rotation. */
export async function loadAndRefreshSession(): Promise<PairingSession | null> {
  const generation = sessionGeneration;
  // The read may queue behind a write. Remember the rotation this unlock joined
  // even if it finishes before authenticated storage returns its new token.
  const pending = [...refreshesInFlight.values()].filter((entry) => entry.generation === generation);
  const stored = await loadStoredSession();
  if (generation !== sessionGeneration) throw new SessionChangedError();
  if (!stored) return null;
  const joinedRefresh = pending.find((entry) => sameSessionIdentity(entry.session, stored));
  const refreshed = joinedRefresh
    ? await joinedRefresh.promise
    : await refreshAndStoreSession(stored);
  if (!refreshed || generation !== sessionGeneration) throw new SessionChangedError();
  return refreshed;
}

/**
 * The shared operation owns both the network rotation and its durable write.
 * Effect cleanup only stops UI updates; it must not release this operation and
 * let another effect send the same single-use refresh token.
 */
export function refreshAndStoreSession(session: PairingSession): Promise<PairingSession | null> {
  const generation = sessionGeneration;
  const identity = sessionIdentity(session);
  const refreshInFlight = refreshesInFlight.get(identity);
  if (
    refreshInFlight?.generation === generation &&
    sameSessionIdentity(refreshInFlight.session, session)
  ) {
    return refreshInFlight.promise;
  }

  const entry = { generation, session, promise: Promise.resolve<PairingSession | null>(null) };
  entry.promise = (async () => {
    try {
      const stored = await loadStoredSession();
      if (
        generation !== sessionGeneration ||
        !stored ||
        !sameSessionIdentity(stored, session) ||
        stored.token !== session.token ||
        stored.refreshToken !== session.refreshToken
      ) return null;

      const nextSession = await refreshMobileSession(stored);
      if (generation !== sessionGeneration) return null;
      const replaced = await replaceSessionIfTokenMatches(stored.token, nextSession);
      if (!replaced || generation !== sessionGeneration) return null;
      return nextSession;
    } catch (error) { // broad-exception-boundary
      // A late failure belongs to the old pairing and must not clear a new one.
      if (generation !== sessionGeneration) return null;
      throw error;
    } finally {
      if (refreshesInFlight.get(identity) === entry) refreshesInFlight.delete(identity);
    }
  })();
  refreshesInFlight.set(identity, entry);
  return entry.promise;
}

/** Invalidate unfinished refreshes as soon as a pairing/clear is requested. */
export function saveSession(session: PairingSession): Promise<void> {
  sessionGeneration += 1;
  return saveStoredSession(session);
}

export function clearSession(): Promise<void> {
  sessionGeneration += 1;
  return clearStoredSession();
}

function sameSessionIdentity(left: PairingSession, right: PairingSession): boolean {
  return sessionIdentity(left) === sessionIdentity(right);
}

function sessionIdentity(session: PairingSession): string {
  return JSON.stringify([session.baseUrl, session.deviceId, session.tokenFamilyId, session.deviceCredentialId]);
}
