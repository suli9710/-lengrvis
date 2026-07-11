import type { BaseUrlSecurity, TlsPinRecord, TlsPinStatus } from "./types";

declare const require: ((id: string) => unknown) | undefined;

type NativeTlsTrustModule = {
  stageServerCertificate?: (
    baseUrl: string,
    fingerprintSha256: string,
    activeExpiresAtEpochMs: number,
    nextExpiresAtEpochMs: number,
    sourceDeviceId: string | null,
  ) => Promise<unknown>;
  assertServerCertificateTrusted?: (baseUrl: string, fingerprintSha256: string) => Promise<unknown>;
  activateServerCertificate?: (
    baseUrl: string,
    fingerprintSha256: string,
    activeExpiresAtEpochMs: number,
    sourceDeviceId: string | null,
  ) => Promise<unknown>;
  revokeServerCertificate?: (baseUrl: string, fingerprintSha256: string) => Promise<unknown>;
  listServerCertificatePins?: (baseUrl: string, includeRevoked: boolean) => Promise<unknown>;
  clearTrustedServers?: () => Promise<unknown>;
};

type ReactNativeRuntime = {
  NativeModules?: {
    LengrvisLanTrust?: NativeTlsTrustModule;
  };
  Platform?: {
    OS?: string;
  };
};

export class TlsTrustConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TlsTrustConfigurationError";
  }
}

const ACTIVE_PIN_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const NEXT_PIN_TTL_MS = 24 * 60 * 60 * 1000;
const TLS_PIN_RECORD_SCHEMA = "tls-pin-record-v1" as const;

export async function stageNativeTlsTrust(
  security: BaseUrlSecurity,
  sourceDeviceId?: string,
): Promise<TlsPinRecord | undefined> {
  const input = tlsPinInput(security);
  if (!input) return undefined;
  const module = requiredNativeTlsTrustModule();
  if (!module.stageServerCertificate) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust module cannot stage certificate pins.");
  }
  const now = Date.now();
  const record = await module.stageServerCertificate(
    input.origin,
    input.fingerprint,
    pinExpiryEpochMs(security, now, ACTIVE_PIN_TTL_MS),
    pinExpiryEpochMs(security, now, NEXT_PIN_TTL_MS),
    normalizedSourceDeviceId(sourceDeviceId),
  );
  return parseTlsPinRecord(record, input, { requireUsable: true });
}

// Existing callers use this before every request. It deliberately verifies an
// already confirmed pin and never creates, renews, or promotes trust.
export async function configureNativeTlsTrust(security: BaseUrlSecurity): Promise<TlsPinRecord | undefined> {
  const input = tlsPinInput(security);
  if (!input) return undefined;
  const module = requiredNativeTlsTrustModule();
  if (!module.assertServerCertificateTrusted) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust module cannot verify certificate pin state.");
  }
  const record = await module.assertServerCertificateTrusted(input.origin, input.fingerprint);
  return parseTlsPinRecord(record, input, { requireUsable: true });
}

export async function activateNativeTlsTrust(
  security: BaseUrlSecurity,
  sourceDeviceId?: string,
): Promise<TlsPinRecord | undefined> {
  const input = tlsPinInput(security);
  if (!input) return undefined;
  const module = requiredNativeTlsTrustModule();
  if (!module.activateServerCertificate) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust module cannot activate certificate pins.");
  }
  const record = await module.activateServerCertificate(
    input.origin,
    input.fingerprint,
    pinExpiryEpochMs(security, Date.now(), ACTIVE_PIN_TTL_MS),
    normalizedSourceDeviceId(sourceDeviceId),
  );
  return parseTlsPinRecord(record, input, { requireStatus: "active", requireUsable: true });
}

export async function revokeNativeTlsPin(baseUrl: string, fingerprintSha256: string): Promise<void> {
  if (!isAndroidRuntime()) return;
  const module = nativeTlsTrustModule();
  if (!module?.revokeServerCertificate) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust module cannot revoke a certificate pin.");
  }
  const revoked = await module.revokeServerCertificate(
    normalizedHttpsOrigin(baseUrl),
    normalizedFingerprint(fingerprintSha256),
  );
  if (revoked !== true) {
    throw new TlsTrustConfigurationError("Android LAN TLS certificate pin was not found or was already revoked.");
  }
}

export async function listNativeTlsPins(baseUrl: string, includeRevoked = false): Promise<TlsPinRecord[]> {
  if (!isAndroidRuntime()) return [];
  const module = nativeTlsTrustModule();
  if (!module?.listServerCertificatePins) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust module cannot list certificate pins.");
  }
  const origin = normalizedHttpsOrigin(baseUrl);
  const value = await module.listServerCertificatePins(origin, includeRevoked);
  if (!Array.isArray(value)) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned an invalid pin list.");
  }
  return value.map((record) => parseTlsPinRecord(record, { origin }));
}

export async function clearNativeTlsTrust(): Promise<void> {
  if (!isAndroidRuntime()) return;
  const module = nativeTlsTrustModule();
  if (!module?.clearTrustedServers) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust module cannot clear certificate pins.");
  }
  await module.clearTrustedServers();
}

function isAndroidRuntime(): boolean {
  return reactNativeRuntime()?.Platform?.OS === "android";
}

function isIosRuntime(): boolean {
  return reactNativeRuntime()?.Platform?.OS === "ios";
}

function nativeTlsTrustModule(): NativeTlsTrustModule | undefined {
  return reactNativeRuntime()?.NativeModules?.LengrvisLanTrust;
}

function requiredNativeTlsTrustModule(): NativeTlsTrustModule {
  if (isIosRuntime()) {
    throw new TlsTrustConfigurationError("iOS LAN certificate pinning is not available yet. Use a system-trusted HTTPS certificate before pairing this iPhone.");
  }
  if (!isAndroidRuntime()) {
    throw new TlsTrustConfigurationError("This mobile runtime cannot configure LAN certificate pinning for local HTTPS pairing.");
  }
  const module = nativeTlsTrustModule();
  if (!module) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust module is unavailable.");
  }
  return module;
}

function tlsPinInput(security: BaseUrlSecurity): { origin: string; fingerprint: string } | undefined {
  if (!security.requiresTlsTrust) return undefined;
  const fingerprint = security.serverTls?.fingerprintSha256;
  if (!fingerprint?.trim()) {
    throw new TlsTrustConfigurationError("LAN HTTPS requires a certificate SHA-256 fingerprint before mobile pairing can trust this computer.");
  }
  const origin = normalizedHttpsOrigin(security.normalizedBaseUrl);
  if (canonicalHost(new URL(origin).hostname) !== canonicalHost(security.hostname)) {
    throw new TlsTrustConfigurationError("LAN HTTPS pin origin does not match the advertised computer host.");
  }
  return { origin, fingerprint: normalizedFingerprint(fingerprint) };
}

function normalizedHttpsOrigin(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new TlsTrustConfigurationError("LAN TLS pins require a valid HTTPS origin.");
  }
  if (parsed.protocol !== "https:") {
    throw new TlsTrustConfigurationError("LAN TLS pins are only accepted for HTTPS origins.");
  }
  if (parsed.username || parsed.password || (parsed.pathname && parsed.pathname !== "/") || parsed.search || parsed.hash) {
    throw new TlsTrustConfigurationError("LAN TLS pins must be scoped to an HTTPS origin without credentials, path, query, or fragment.");
  }
  return parsed.origin;
}

function normalizedFingerprint(value: string): string {
  const fingerprint = value.trim().replace(/[:\s]/g, "").toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(fingerprint)) {
    throw new TlsTrustConfigurationError("Certificate SHA-256 fingerprint must contain 64 hex characters.");
  }
  return fingerprint;
}

function normalizedSourceDeviceId(value?: string): string | null {
  const normalized = value?.trim();
  return normalized ? normalized.slice(0, 128) : null;
}

function pinExpiryEpochMs(security: BaseUrlSecurity, now: number, ttlMs: number): number {
  const certificateExpiry = Date.parse(security.serverTls?.validTo ?? "");
  const expiresAt = Number.isFinite(certificateExpiry) ? Math.min(now + ttlMs, certificateExpiry) : now + ttlMs;
  if (expiresAt <= now) {
    throw new TlsTrustConfigurationError("The advertised LAN HTTPS certificate is already expired.");
  }
  return expiresAt;
}

function parseTlsPinRecord(
  value: unknown,
  expected: { origin: string; fingerprint?: string },
  requirements: { requireStatus?: TlsPinStatus; requireUsable?: boolean } = {},
): TlsPinRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned an invalid pin record.");
  }
  const record = value as Record<string, unknown>;
  const status = record.status;
  if (record.schema_version !== TLS_PIN_RECORD_SCHEMA || (status !== "active" && status !== "next" && status !== "revoked")) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned an unsupported pin record.");
  }
  const origin = normalizedHttpsOrigin(requiredText(record.origin, "origin"));
  const host = canonicalHost(requiredText(record.host, "host"));
  const fingerprint = normalizedFingerprint(requiredText(record.fingerprint_sha256, "fingerprint_sha256"));
  const pinId = requiredText(record.pin_id, "pin_id");
  const createdAt = requiredTimestamp(record.created_at, "created_at");
  const expiresAt = requiredTimestamp(record.expires_at, "expires_at");
  const revokedAt = optionalTimestamp(record.revoked_at, "revoked_at");
  if (origin !== expected.origin || host !== canonicalHost(new URL(origin).hostname)) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned a pin for a different origin.");
  }
  if (expected.fingerprint && fingerprint !== expected.fingerprint) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned a different certificate fingerprint.");
  }
  if (Date.parse(expiresAt) <= Date.parse(createdAt)) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned an invalid pin lifetime.");
  }
  const lifetimeMs = Date.parse(expiresAt) - Date.parse(createdAt);
  const maximumLifetimeMs = status === "next" ? NEXT_PIN_TTL_MS : ACTIVE_PIN_TTL_MS + NEXT_PIN_TTL_MS;
  if (lifetimeMs > maximumLifetimeMs || Date.parse(createdAt) > Date.now() + 5 * 60 * 1000) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned an invalid pin lifetime.");
  }
  if (status === "revoked" && !revokedAt) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned a revoked pin without revocation time.");
  }
  if (status !== "revoked" && revokedAt) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned a usable pin with a revocation time.");
  }
  if (pinId.length > 128) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned an invalid pin identifier.");
  }
  if (requirements.requireStatus && status !== requirements.requireStatus) {
    throw new TlsTrustConfigurationError(`Android LAN TLS trust expected a ${requirements.requireStatus} pin.`);
  }
  if (requirements.requireUsable && (status === "revoked" || Date.parse(expiresAt) <= Date.now())) {
    throw new TlsTrustConfigurationError("Android LAN TLS certificate pin is expired or revoked.");
  }
  const sourceDeviceId = optionalText(record.source_device_id);
  if (sourceDeviceId && sourceDeviceId.length > 128) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust returned an invalid source device identifier.");
  }
  return {
    schema_version: TLS_PIN_RECORD_SCHEMA,
    pin_id: pinId,
    origin,
    host,
    fingerprint_sha256: fingerprint,
    status,
    created_at: createdAt,
    expires_at: expiresAt,
    ...(sourceDeviceId ? { source_device_id: sourceDeviceId } : {}),
    ...(revokedAt ? { revoked_at: revokedAt } : {}),
  };
}

function requiredText(value: unknown, field: string): string {
  const normalized = optionalText(value);
  if (!normalized) throw new TlsTrustConfigurationError(`Android LAN TLS pin record is missing ${field}.`);
  return normalized;
}

function optionalText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function canonicalHost(value: string): string {
  return value.trim().replace(/^\[|\]$/g, "").toLowerCase();
}

function requiredTimestamp(value: unknown, field: string): string {
  const normalized = optionalTimestamp(value, field);
  if (!normalized) throw new TlsTrustConfigurationError(`Android LAN TLS pin record is missing ${field}.`);
  return normalized;
}

function optionalTimestamp(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) {
    throw new TlsTrustConfigurationError(`Android LAN TLS pin record has an invalid ${field}.`);
  }
  return value;
}

function reactNativeRuntime(): ReactNativeRuntime | undefined {
  if (typeof require !== "function") return undefined;
  try {
    return require("react-native") as ReactNativeRuntime;
  } catch {
    return undefined;
  }
}
