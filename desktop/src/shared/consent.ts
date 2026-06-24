/**
 * Shared consent types and constants for legal document display.
 *
 * Used by main process (consent IPC handler), preload (bridge),
 * and renderer (privacy modal + settings about page).
 */

/** Current EULA and privacy policy versions. Bump when legal docs change. */
export const LEGAL_VERSIONS = {
  eula: "v1.0",
  privacy: "v1.1",
} as const;

/** Legal document identifiers for the about/settings page. */
export type LegalDocId = "privacy-policy" | "eula" | "notice";

/** Shape of consent.json stored in ${LENGRVIS_DATA_DIR}/consent.json. */
export interface ConsentRecord {
  eula_version: string;
  eula_accepted_at: string | null;
  privacy_version: string;
  privacy_accepted_at: string | null;
  installer_version: string | null;
  platform: string | null;
}

/** Request payload for accepting legal consent (EULA and/or privacy policy). */
export interface AcceptConsentRequest {
  acceptEula?: boolean;
  acceptPrivacy?: boolean;
  installerVersion?: string;
}

/** Result of checking whether the user needs to (re-)accept legal documents. */
export interface ConsentStatusResult {
  consent: ConsentRecord | null;
  needsEulaConsent: boolean;
  needsPrivacyConsent: boolean;
  currentVersions: typeof LEGAL_VERSIONS;
}

/**
 * Determine whether the user needs to re-accept the privacy policy.
 *
 * Re-consent is triggered when:
 * - No consent record exists yet (first launch).
 * - The stored privacy_version differs from the current one.
 *
 * Patch-version changes (v1.0 → v1.0.1) do NOT trigger re-consent
 * because only the major.minor portion is compared.
 */
export function needsPrivacyReconsent(record: ConsentRecord | null): boolean {
  if (!record || !record.privacy_accepted_at) {
    return true;
  }
  return compareMajorMinor(record.privacy_version, LEGAL_VERSIONS.privacy) !== 0;
}

/**
 * Determine whether the user needs to re-accept the EULA.
 * Same logic as needsPrivacyReconsent but for the EULA version.
 */
export function needsEulaReconsent(record: ConsentRecord | null): boolean {
  if (!record || !record.eula_accepted_at) {
    return true;
  }
  return compareMajorMinor(record.eula_version, LEGAL_VERSIONS.eula) !== 0;
}

export function mergeConsentRecord(
  existing: ConsentRecord | null,
  patch: Partial<ConsentRecord>,
  platform: string
): ConsentRecord {
  return {
    eula_version: patch.eula_version ?? existing?.eula_version ?? LEGAL_VERSIONS.eula,
    eula_accepted_at: patch.eula_accepted_at !== undefined
      ? patch.eula_accepted_at
      : existing?.eula_accepted_at ?? null,
    privacy_version: patch.privacy_version ?? existing?.privacy_version ?? LEGAL_VERSIONS.privacy,
    privacy_accepted_at: patch.privacy_accepted_at !== undefined
      ? patch.privacy_accepted_at
      : existing?.privacy_accepted_at ?? null,
    installer_version: patch.installer_version !== undefined
      ? patch.installer_version
      : existing?.installer_version ?? null,
    platform: patch.platform !== undefined ? patch.platform : existing?.platform ?? platform,
  };
}

/**
 * Compare only the major.minor portion of two semver-ish strings.
 * Returns: -1 if a < b, 0 if equal, 1 if a > b.
 */
function compareMajorMinor(a: string, b: string): number {
  const pa = parseMajorMinor(a);
  const pb = parseMajorMinor(b);
  if (pa.major !== pb.major) return pa.major < pb.major ? -1 : 1;
  if (pa.minor !== pb.minor) return pa.minor < pb.minor ? -1 : 1;
  return 0;
}

function parseMajorMinor(version: string): { major: number; minor: number } {
  // Strip leading 'v' and take the first two numeric components.
  const cleaned = version.replace(/^v/i, "");
  const parts = cleaned.split(".");
  return {
    major: Number.parseInt(parts[0], 10) || 0,
    minor: Number.parseInt(parts[1], 10) || 0,
  };
}
