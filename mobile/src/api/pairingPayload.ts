import { describeBaseUrlSecurity, normalizeBaseUrl, type BaseUrlSecurity } from "./client";

export type PairingPayloadSource = "json" | "url" | "text";

export interface PairingPayload {
  baseUrl: string;
  code: string;
  expiresAt?: string;
  source: PairingPayloadSource;
}

export type PairingPayloadSecurityStatus = "ready" | "requires_https_wss" | "loopback" | "expired" | "invalid_address";

export interface PairingPayloadSecurityState {
  status: PairingPayloadSecurityStatus;
  canPair: boolean;
  security?: BaseUrlSecurity;
}

export type PairingPayloadParseErrorCode = "empty" | "missing_code" | "missing_address" | "invalid_address";

export class PairingPayloadParseError extends Error {
  readonly code: PairingPayloadParseErrorCode;

  constructor(code: PairingPayloadParseErrorCode, message: string) {
    super(message);
    this.name = "PairingPayloadParseError";
    this.code = code;
  }
}

export function parsePairingPayload(value: string): PairingPayload {
  const raw = value.trim();
  if (!raw) {
    throw new PairingPayloadParseError("empty", "Pairing payload is empty.");
  }

  const json = parseJsonPayload(raw);
  if (json) return json;

  const url = parseUrlPayload(raw);
  if (url) return url;

  const text = parsePlainTextPayload(raw);
  if (text) return text;

  throw new PairingPayloadParseError("missing_code", "Pairing payload must include a 6-character code and a computer address.");
}

export function classifyPairingPayloadSecurity(payload: Pick<PairingPayload, "baseUrl" | "expiresAt">, nowMs = Date.now()): PairingPayloadSecurityState {
  try {
    const security = describeBaseUrlSecurity(payload.baseUrl);
    if (security.isInsecureLan) {
      return { status: "requires_https_wss", canPair: false, security };
    }
    if (security.isLoopback) {
      return { status: "loopback", canPair: false, security };
    }
    if (isExpiredPairingPayload(payload.expiresAt, nowMs)) {
      return { status: "expired", canPair: false, security };
    }
    return { status: "ready", canPair: true, security };
  } catch {
    return { status: "invalid_address", canPair: false };
  }
}

function isExpiredPairingPayload(expiresAt: string | undefined, nowMs: number): boolean {
  if (!expiresAt) return false;
  const expiryMs = Date.parse(expiresAt);
  return Number.isFinite(expiryMs) && expiryMs <= nowMs;
}

function parseJsonPayload(raw: string): PairingPayload | null {
  const decoded = decodePossiblyEncoded(raw);
  for (const candidate of uniqueCandidates([raw, decoded])) {
    try {
      const parsed = JSON.parse(candidate) as unknown;
      const payload = payloadFromRecord(asRecord(parsed), "json");
      if (payload) return payload;
    } catch (error) {
      if (error instanceof PairingPayloadParseError) throw error;
      // Try URL and plain-text formats below.
    }
  }
  return null;
}

function parseUrlPayload(raw: string): PairingPayload | null {
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return null;
  }

  const params = parsed.searchParams;
  const code = normalizePairingCode(firstParam(params, "code", "pair_code", "pairCode", "pairing_code", "pairingCode"));
  const baseUrl =
    firstParam(params, "base_url", "baseUrl", "url", "origin", "server", "server_url", "serverUrl", "server_origin", "serverOrigin") ??
    baseUrlFromParts({
      scheme: firstParam(params, "scheme", "protocol"),
      host: firstParam(params, "host", "hostname"),
      port: firstParam(params, "port"),
    }) ??
    (parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.origin : "");

  return normalizePayloadParts({ baseUrl, code, expiresAt: firstParam(params, "expires_at", "expiresAt"), source: "url" });
}

function parsePlainTextPayload(raw: string): PairingPayload | null {
  const baseUrl = firstLabeledAddress(raw) ?? firstAddress(raw);
  const withoutAddress = baseUrl ? raw.replace(baseUrl, " ") : raw;
  const code = normalizePairingCode(firstLabeledCode(raw) ?? firstStandaloneCode(withoutAddress));
  return normalizePayloadParts({ baseUrl, code, source: "text" });
}

function payloadFromRecord(record: Record<string, unknown> | undefined, source: PairingPayloadSource): PairingPayload | null {
  if (!record) return null;
  const server = firstRecord(record, "server", "backend", "desktop", "computer");
  const transport = firstRecord(record, "transport_security", "transportSecurity", "transport") ?? firstRecord(server, "transport_security", "transportSecurity", "transport");
  const pairing = firstRecord(record, "pairing", "pair", "mobile_pairing", "mobilePairing");
  const code = normalizePairingCode(
    firstText(pairing, "code", "pair_code", "pairCode", "pairing_code", "pairingCode") ??
      firstText(record, "code", "pair_code", "pairCode", "pairing_code", "pairingCode"),
  );
  const baseUrl =
    firstText(record, "base_url", "baseUrl", "origin", "server_origin", "serverOrigin", "url", "server_url", "serverUrl") ??
    firstText(server, "origin", "base_url", "baseUrl", "url", "server_url", "serverUrl") ??
    firstText(transport, "origin", "advertised_base_url", "advertisedBaseUrl", "base_url", "baseUrl", "server_url", "serverUrl") ??
    baseUrlFromParts({
      scheme: firstText(server, "scheme", "protocol") ?? firstText(transport, "scheme", "protocol"),
      host: firstText(server, "host", "hostname"),
      port: firstText(server, "port"),
    });
  const expiresAt = firstText(record, "expires_at", "expiresAt") ?? firstText(pairing, "expires_at", "expiresAt");
  return normalizePayloadParts({ baseUrl, code, expiresAt, source });
}

function normalizePayloadParts(parts: {
  baseUrl?: string;
  code?: string;
  expiresAt?: string;
  source: PairingPayloadSource;
}): PairingPayload | null {
  if (!parts.baseUrl && !parts.code) return null;
  if (!parts.baseUrl) {
    throw new PairingPayloadParseError("missing_address", "Pairing payload is missing the computer address.");
  }
  if (!parts.code) {
    throw new PairingPayloadParseError("missing_code", "Pairing payload is missing the pairing code.");
  }
  try {
    return {
      baseUrl: normalizeBaseUrl(parts.baseUrl),
      code: parts.code,
      ...(parts.expiresAt ? { expiresAt: parts.expiresAt } : {}),
      source: parts.source,
    };
  } catch {
    throw new PairingPayloadParseError("invalid_address", "Pairing payload contains an invalid computer address.");
  }
}

function firstLabeledAddress(value: string): string | undefined {
  const match = value.match(/(?:电脑地址|服务器|地址|server|origin|url|base\s*url)\s*[:：]\s*(https?:\/\/[^\s"'<>，。；;、·）)]+)/i);
  return match?.[1];
}

function firstAddress(value: string): string | undefined {
  const urlMatch = value.match(/https?:\/\/[^\s"'<>，。；;、·）)]+/i);
  if (urlMatch) return urlMatch[0];
  const hostPortMatch = value.match(/\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b/);
  if (hostPortMatch) return `http://${hostPortMatch[0]}`;
  return undefined;
}

function firstLabeledCode(value: string): string | undefined {
  const match = value.match(/(?:配对码|pair(?:ing)?\s*code|code)\s*[:：#-]?\s*([a-z0-9]{6})/i);
  return match?.[1];
}

function firstStandaloneCode(value: string): string | undefined {
  const matches = value.match(/\b[a-z0-9]{6}\b/gi) ?? [];
  const blocked = new Set(["server", "origin", "mobile", "pairin", "lengrv"]);
  return matches.find((candidate) => !blocked.has(candidate.toLowerCase()));
}

function normalizePairingCode(value: string | undefined): string | undefined {
  const normalized = value?.replace(/[^a-z0-9]/gi, "").toLowerCase();
  return normalized && normalized.length === 6 ? normalized : undefined;
}

function baseUrlFromParts(parts: { scheme?: string; host?: string; port?: string }): string | undefined {
  if (!parts.host) return undefined;
  const scheme = normalizeScheme(parts.scheme);
  const port = parts.port ? `:${parts.port}` : "";
  return `${scheme}://${parts.host}${port}`;
}

function normalizeScheme(value: string | undefined): "http" | "https" {
  const normalized = value?.trim().toLowerCase().replace(/:$/, "");
  return normalized === "https" ? "https" : "http";
}

function firstParam(params: URLSearchParams, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = params.get(key);
    if (value?.trim()) return value.trim();
  }
  return undefined;
}

function firstText(value: Record<string, unknown> | undefined, ...keys: string[]): string | undefined {
  if (!value) return undefined;
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
    if (typeof candidate === "number" || typeof candidate === "boolean") return String(candidate);
  }
  return undefined;
}

function firstRecord(value: Record<string, unknown> | undefined, ...keys: string[]): Record<string, unknown> | undefined {
  if (!value) return undefined;
  for (const key of keys) {
    const candidate = asRecord(value[key]);
    if (candidate) return candidate;
  }
  return undefined;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function decodePossiblyEncoded(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function uniqueCandidates(values: string[]): string[] {
  return values.filter((value, index, all) => value && all.indexOf(value) === index);
}
