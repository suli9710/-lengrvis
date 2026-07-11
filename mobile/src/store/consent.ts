/**
 * Mobile consent store — manages EULA and privacy policy acceptance state.
 *
 * Uses SecureStore (expo-secure-store) for tamper-resistant consent records.
 * Falls back to in-memory Map when SecureStore native module is unavailable.
 */

import * as SecureStore from "expo-secure-store";

const EULA_CONSENT_KEY = "lengrvis.mobile.consent.eula";
const PRIVACY_CONSENT_KEY = "lengrvis.mobile.consent.privacy";
const EULA_VERSION_KEY = "lengrvis.mobile.consent.eula_version";
const PRIVACY_VERSION_KEY = "lengrvis.mobile.consent.privacy_version";
const EULA_ACCEPTED_AT_KEY = "lengrvis.mobile.consent.eula_accepted_at";
const PRIVACY_ACCEPTED_AT_KEY = "lengrvis.mobile.consent.privacy_accepted_at";

export const MOBILE_LEGAL_VERSIONS = {
  eula: "v1.0",
  privacy: "v1.2",
} as const;

const memoryStore = new Map<string, string>();

/** Check if secure storage backend is available (catches native module errors). */
function isStorageUnavailable(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /Native\s*module|NativeModule|NativeModuleError|Expo(?:nent)?SecureStore|SecureStore.*unavailable|Cannot find native module|TurboModuleRegistry.*not found|module.*not found|not available|not supported/i.test(message);
}

async function secureGet(key: string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(key);
  } catch (error) { // broad-exception-boundary
    if (isStorageUnavailable(error)) {
      return memoryStore.get(key) ?? null;
    }
    throw error;
  }
}

async function secureSet(key: string, value: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(key, value);
  } catch (error) { // broad-exception-boundary
    if (isStorageUnavailable(error)) {
      memoryStore.set(key, value);
      return;
    }
    throw error;
  }
}

export interface ConsentState {
  eulaAccepted: boolean;
  privacyAccepted: boolean;
  eulaAcceptedAt: string | null;
  privacyAcceptedAt: string | null;
  needsConsent: boolean;
}

/** Load the current consent state from SecureStore. */
export async function loadConsentState(): Promise<ConsentState> {
  const [eulaConsent, privacyConsent, eulaVersion, privacyVersion, eulaAcceptedAtRaw, privacyAcceptedAtRaw] = await Promise.all([
    secureGet(EULA_CONSENT_KEY),
    secureGet(PRIVACY_CONSENT_KEY),
    secureGet(EULA_VERSION_KEY),
    secureGet(PRIVACY_VERSION_KEY),
    secureGet(EULA_ACCEPTED_AT_KEY),
    secureGet(PRIVACY_ACCEPTED_AT_KEY),
  ]);

  const eulaAcceptedAt = validConsentTimestamp(eulaAcceptedAtRaw);
  const privacyAcceptedAt = validConsentTimestamp(privacyAcceptedAtRaw);
  const eulaAccepted = eulaConsent === "true" && eulaVersion === MOBILE_LEGAL_VERSIONS.eula && Boolean(eulaAcceptedAt);
  const privacyAccepted = privacyConsent === "true" && privacyVersion === MOBILE_LEGAL_VERSIONS.privacy && Boolean(privacyAcceptedAt);
  const needsConsent = !eulaAccepted || !privacyAccepted;

  return { eulaAccepted, privacyAccepted, eulaAcceptedAt, privacyAcceptedAt, needsConsent };
}

/** Record EULA and/or privacy consent. Writes version-stamped records. */
export async function acceptConsent(opts: { eula?: boolean; privacy?: boolean }): Promise<void> {
  const timestamp = new Date().toISOString();
  const tasks: Array<Promise<void>> = [];
  if (opts.eula) {
    tasks.push(secureSet(EULA_CONSENT_KEY, "true"));
    tasks.push(secureSet(EULA_VERSION_KEY, MOBILE_LEGAL_VERSIONS.eula));
    tasks.push(secureSet(EULA_ACCEPTED_AT_KEY, timestamp));
  }
  if (opts.privacy) {
    tasks.push(secureSet(PRIVACY_CONSENT_KEY, "true"));
    tasks.push(secureSet(PRIVACY_VERSION_KEY, MOBILE_LEGAL_VERSIONS.privacy));
    tasks.push(secureSet(PRIVACY_ACCEPTED_AT_KEY, timestamp));
  }
  await Promise.all(tasks);
}

/** Clear all consent records (used on unpair/reset). */
export async function clearConsent(): Promise<void> {
  const keys = [
    EULA_CONSENT_KEY,
    PRIVACY_CONSENT_KEY,
    EULA_VERSION_KEY,
    PRIVACY_VERSION_KEY,
    EULA_ACCEPTED_AT_KEY,
    PRIVACY_ACCEPTED_AT_KEY,
  ];
  await Promise.all(keys.map((key) => secureDelete(key)));
}

function validConsentTimestamp(value: string | null): string | null {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  return value;
}

async function secureDelete(key: string): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(key);
  } catch (error) { // broad-exception-boundary
    if (isStorageUnavailable(error)) {
      memoryStore.delete(key);
      return;
    }
    throw error;
  }
}
