import type { BaseUrlSecurity, BaseUrlSecurityKind, PairResult, PairingSecurityMetadata, PairingServerInfo, PairingSession, PairingTransportMetadata, ServerTlsTrustInfo, ServerTlsTrustStatus, WebSocketConnectionInfo } from "./types";
import { AuthExpiredError, ForbiddenError, InsecureLanBaseUrlError, invalidPairingResponse } from "./http";
import { BACKEND_TLS_DISABLED_WARNING, INSECURE_LAN_HTTP_WARNING, MOBILE_AUTH_WS_PROTOCOL_PREFIX, SELF_SIGNED_TLS_WARNING, SESSION_TOKEN_EXPIRY_SKEW_MS, WEB_SOCKET_SUBPROTOCOL_TOKEN_PATTERN } from "./types";

export function approvalWebSocketConnectionInfo(session: PairingSession): WebSocketConnectionInfo {
  const safeSession = assertSafePairingSession(session);
  return webSocketConnectionInfo(safeSession, "/ws/mobile/approvals", mobileAuthWebSocketProtocols(safeSession));
}

export function remoteScreenWebSocketConnectionInfo(session: PairingSession): WebSocketConnectionInfo {
  const safeSession = assertSafePairingSession(session);
  return webSocketConnectionInfo(safeSession, "/ws/remote/screen", mobileAuthWebSocketProtocols(safeSession));
}

export function remoteInputWebSocketConnectionInfo(session: PairingSession, token: string): WebSocketConnectionInfo {
  const safeSession = assertSafePairingSession(session);
  return webSocketConnectionInfo(safeSession, "/ws/remote/input", mobileTokenWebSocketProtocols(token));
}

export function approvalWebSocketUrl(session: PairingSession): string {
  return approvalWebSocketConnectionInfo(session).url;
}

export function remoteScreenWebSocketUrl(session: PairingSession): string {
  return remoteScreenWebSocketConnectionInfo(session).url;
}

export function remoteInputWebSocketUrl(session: PairingSession): string {
  return webSocketConnectionInfo(session, "/ws/remote/input").url;
}

export function mobileAuthWebSocketProtocols(session: PairingSession): string[] {
  const safeSession = assertSafePairingSession(session);
  assertWebSocketSubprotocolToken(safeSession.token);
  return webSocketProtocolList(`${MOBILE_AUTH_WS_PROTOCOL_PREFIX}${safeSession.token}`);
}

export function mobileTokenWebSocketProtocols(token: string): string[] {
  assertWebSocketSubprotocolToken(token);
  return webSocketProtocolList(`${MOBILE_AUTH_WS_PROTOCOL_PREFIX}${token}`);
}

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error("请输入 Lengrvis 中显示的电脑地址。");
  const hasProtocol = /^[a-z][a-z\d+\-.]*:\/\//i.test(trimmed);
  if (hasProtocol && !/^https?:\/\//i.test(trimmed)) {
    throw new Error("电脑地址必须以 http:// 或 https:// 开头。");
  }
  const withProtocol = hasProtocol ? trimmed : `http://${trimmed}`;
  const parsed = new URL(withProtocol);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("电脑地址必须以 http:// 或 https:// 开头。");
  }
  return parsed.origin;
}

export function isLoopbackBaseUrl(value: string): boolean {
  try {
    return describeBaseUrlSecurity(value).isLoopback;
  } catch {
    return false;
  }
}

export function describeBaseUrlSecurity(value: string, pairingMetadata?: unknown): BaseUrlSecurity {
  const normalizedBaseUrl = normalizeBaseUrl(value);
  const parsed = new URL(normalizedBaseUrl);
  const protocol = parsed.protocol as "http:" | "https:";
  const hostname = parsed.hostname.toLowerCase();
  const isHttps = protocol === "https:";
  const isLoopback = isLoopbackHostname(hostname);
  const isInsecureLan = protocol === "http:" && !isLoopback;
  const kind: BaseUrlSecurityKind = isHttps ? "https" : isLoopback ? "loopbackHttp" : "insecureLan";
  const security: BaseUrlSecurity = {
    kind,
    normalizedBaseUrl,
    protocol,
    webSocketProtocol: isHttps ? "wss:" : "ws:",
    host: parsed.host,
    hostname,
    isHttps,
    isLoopback,
    isInsecureLan,
    backendTlsEnabled: isHttps,
    requiresTlsTrust: false,
    requiresExplicitAllow: isInsecureLan,
    warning: isInsecureLan ? INSECURE_LAN_HTTP_WARNING : undefined,
  };
  return mergeBaseUrlSecurityMetadata(security, normalizePairingSecurityMetadata(pairingMetadata, security));
}

// Insecure LAN base URLs are always rejected; there is intentionally no opt-out
// (see mobile-token-smoke.cjs which asserts `allowInsecureLan` has no effect).
export function assertSafeBaseUrl(value: string): BaseUrlSecurity {
  const security = describeBaseUrlSecurity(value);
  if (security.isInsecureLan) {
    throw new InsecureLanBaseUrlError(security);
  }
  return security;
}

export function describeSessionBaseUrlSecurity(session: Pick<PairingSession, "baseUrl" | "baseUrlSecurity" | "security">): BaseUrlSecurity {
  const pairingMetadata =
    session.security ??
    session.baseUrlSecurity?.backendSecurity ??
    (session.baseUrlSecurity?.serverTls ? { tls: session.baseUrlSecurity.serverTls } : undefined);
  return describeBaseUrlSecurity(session.baseUrl, pairingMetadata);
}

export function assertSafePairingSession(session: PairingSession): PairingSession {
  const baseUrlSecurity = describeSessionBaseUrlSecurity(session);
  if (baseUrlSecurity.isInsecureLan || sessionHasUnsafeRemoteTransport(baseUrlSecurity)) {
    throw new InsecureLanBaseUrlError(baseUrlSecurity);
  }
  assertUsablePairingToken(session);
  return {
    ...session,
    baseUrl: baseUrlSecurity.normalizedBaseUrl,
    baseUrlSecurity,
    ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
  };
}

export function assertSafeRefreshablePairingSession(session: PairingSession): PairingSession {
  const baseUrlSecurity = describeSessionBaseUrlSecurity(session);
  if (baseUrlSecurity.isInsecureLan || sessionHasUnsafeRemoteTransport(baseUrlSecurity)) {
    throw new InsecureLanBaseUrlError(baseUrlSecurity);
  }
  if (!session.token?.trim()) {
    throw new AuthExpiredError("Mobile access token is missing. Pair this phone again.");
  }
  if (!session.deviceId?.trim() || !session.tokenFamilyId?.trim() || !session.deviceCredentialId?.trim()) {
    throw new AuthExpiredError("Mobile session device binding is missing. Pair this phone again.");
  }
  assertWebSocketSubprotocolToken(session.token);
  assertUsableRefreshToken(session);
  return {
    ...session,
    baseUrl: baseUrlSecurity.normalizedBaseUrl,
    baseUrlSecurity,
    ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
  };
}

export function webSocketConnectionInfo(session: PairingSession, pathname: string, protocols: string[] = []): WebSocketConnectionInfo {
  const safeSession = assertSafePairingSession(session);
  const security = safeSession.baseUrlSecurity;
  if (sessionHasUnsafeRemoteTransport(security)) {
    throw new InsecureLanBaseUrlError(security);
  }
  const url = new URL(pathname, security.normalizedBaseUrl);
  url.protocol = security.webSocketProtocol;
  return {
    url: url.toString(),
    protocols,
    security,
    warning: security.warning,
  };
}

export function normalizePairingSecurityMetadata(value: unknown, fallback?: BaseUrlSecurity): PairingSecurityMetadata | undefined {
  const root = asRecord(value);
  if (!root) return undefined;

  const serverRoot = asRecord(root.server);
  const transportSecurityRoot =
    asRecord(root.transport_security) ??
    asRecord(root.transportSecurity) ??
    asRecord(serverRoot?.transport_security) ??
    asRecord(serverRoot?.transportSecurity);
  const securityRoot = asRecord(root.security) ?? root;
  const transportRoot = asRecord(root.transport) ?? asRecord(securityRoot.transport) ?? transportSecurityRoot ?? serverRoot;
  const tlsRoot =
    asRecord(root.tls) ??
    asRecord(securityRoot.tls) ??
    asRecord(transportSecurityRoot?.tls) ??
    asRecord(root.certificate) ??
    asRecord(securityRoot.certificate) ??
    asRecord(transportSecurityRoot?.certificate) ??
    asRecord(root.cert) ??
    asRecord(securityRoot.cert) ??
    asRecord(transportSecurityRoot?.cert) ??
    asRecord(transportRoot?.tls) ??
    transportSecurityRoot;
  const hasMetadata =
    root.security !== undefined ||
    root.transport !== undefined ||
    root.transport_security !== undefined ||
    root.transportSecurity !== undefined ||
    serverRoot?.transport_security !== undefined ||
    serverRoot?.transportSecurity !== undefined ||
    hasKnownTransportMetadata(securityRoot) ||
    hasKnownTransportMetadata(transportRoot) ||
    hasKnownTransportMetadata(serverRoot) ||
    hasKnownTransportMetadata(transportSecurityRoot) ||
    hasKnownTlsMetadata(securityRoot) ||
    hasKnownTlsMetadata(transportSecurityRoot) ||
    hasKnownTlsMetadata(tlsRoot);
  if (!hasMetadata) return undefined;

  const transport = normalizeTransportMetadata(securityRoot, transportRoot, fallback);
  const tls = normalizeTlsTrustInfo(securityRoot, tlsRoot, transport, fallback);
  const backendTlsEnabled = tls?.enabled ?? transport?.tlsEnabled;
  if (!transport && !tls && backendTlsEnabled === undefined) return undefined;
  return {
    ...(transport ? { transport } : {}),
    ...(tls ? { tls } : {}),
    ...(backendTlsEnabled !== undefined ? { backendTlsEnabled } : {}),
  };
}

export function mergeBaseUrlSecurityMetadata(
  security: BaseUrlSecurity,
  pairingMetadata?: PairingSecurityMetadata,
): BaseUrlSecurity {
  if (!pairingMetadata) return security;
  const serverTls = pairingMetadata.tls;
  const backendTlsEnabled = pairingMetadata.backendTlsEnabled ?? serverTls?.enabled ?? pairingMetadata.transport?.tlsEnabled ?? security.backendTlsEnabled;
  const transportWebSocketProtocol = pairingMetadata.transport?.webSocketScheme === "wss"
    ? "wss:"
    : pairingMetadata.transport?.webSocketScheme === "ws"
      ? "ws:"
      : security.webSocketProtocol;
  const remoteTransportUnsafe = !security.isLoopback && (!backendTlsEnabled || transportWebSocketProtocol !== "wss:");
  const requiresTlsTrust = Boolean(serverTls?.requiresTrust);
  const warning =
    security.warning ??
    (requiresTlsTrust
      ? serverTls?.warning ?? SELF_SIGNED_TLS_WARNING
      : remoteTransportUnsafe
        ? BACKEND_TLS_DISABLED_WARNING
        : undefined);
  return {
    ...security,
    kind: remoteTransportUnsafe ? "insecureLan" : security.kind,
    isInsecureLan: security.isInsecureLan || remoteTransportUnsafe,
    backendTlsEnabled,
    webSocketProtocol: transportWebSocketProtocol,
    requiresTlsTrust,
    serverTls,
    backendSecurity: pairingMetadata,
    warning,
  };
}

export function formatTlsFingerprint(fingerprint?: string): string {
  const normalized = normalizeFingerprint(fingerprint);
  if (!normalized) return "";
  if (normalized.includes(":")) return normalized;
  return normalized.match(/.{1,2}/g)?.join(":") ?? normalized;
}

export function normalizePairingServerInfo(server: PairResult["server"] | undefined): PairingServerInfo | undefined {
  if (!server || !server.host || !Number.isFinite(server.port)) return undefined;
  const origin = server.origin ?? server.base_url ?? server.url;
  const transportSecurity = normalizePairingSecurityMetadata(server.transport_security);
  return {
    host: server.host,
    port: server.port,
    ...(server.protocol ?? server.scheme ? { protocol: server.protocol ?? server.scheme } : {}),
    ...(server.scheme ? { scheme: server.scheme } : {}),
    ...(server.url ? { url: server.url } : {}),
    ...(server.base_url ?? origin ? { baseUrl: server.base_url ?? origin } : {}),
    ...(origin ? { origin } : {}),
    ...(transportSecurity ? { transportSecurity } : {}),
  };
}

export function normalizeTransportMetadata(
  securityRoot: Record<string, unknown>,
  transportRoot: Record<string, unknown> | undefined,
  fallback?: BaseUrlSecurity,
): PairingTransportMetadata | undefined {
  const httpScheme = normalizeHttpScheme(
    firstText(transportRoot, "http_scheme", "httpScheme", "scheme", "protocol", "transport") ??
      firstText(securityRoot, "http_scheme", "httpScheme", "scheme", "protocol", "transport"),
  );
  const webSocketScheme = normalizeWebSocketScheme(
    firstText(transportRoot, "websocket_scheme", "webSocketScheme", "ws_scheme", "wsScheme") ??
      firstText(securityRoot, "websocket_scheme", "webSocketScheme", "ws_scheme", "wsScheme"),
  );
  const explicitTlsEnabled =
    firstBoolean(transportRoot, "tls_enabled", "tlsEnabled", "https_enabled", "httpsEnabled") ??
    firstBoolean(securityRoot, "tls_enabled", "tlsEnabled", "https_enabled", "httpsEnabled", "backend_tls_enabled", "backendTlsEnabled");
  const tlsEnabled = explicitTlsEnabled ?? (httpScheme === "https" ? true : httpScheme === "http" ? false : undefined);
  const advertisedBaseUrl =
    firstText(transportRoot, "advertised_base_url", "advertisedBaseUrl", "base_url", "baseUrl", "origin") ??
    firstText(securityRoot, "advertised_base_url", "advertisedBaseUrl", "base_url", "baseUrl", "server_origin", "serverOrigin", "origin");
  const serverUrl =
    firstText(transportRoot, "server_url", "serverUrl", "url", "origin") ??
    firstText(securityRoot, "server_url", "serverUrl", "url", "server_origin", "serverOrigin", "origin");

  if (
    httpScheme === undefined &&
    webSocketScheme === undefined &&
    tlsEnabled === undefined &&
    advertisedBaseUrl === undefined &&
    serverUrl === undefined
  ) {
    return undefined;
  }

  return {
    ...(httpScheme ? { httpScheme } : fallback ? { httpScheme: fallback.isHttps ? "https" : "http" } : {}),
    ...(webSocketScheme ? { webSocketScheme } : fallback ? { webSocketScheme: fallback.isHttps ? "wss" : "ws" } : {}),
    ...(tlsEnabled !== undefined ? { tlsEnabled } : {}),
    ...(advertisedBaseUrl ? { advertisedBaseUrl } : {}),
    ...(serverUrl ? { serverUrl } : {}),
  };
}

export function normalizeTlsTrustInfo(
  securityRoot: Record<string, unknown>,
  tlsRoot: Record<string, unknown> | undefined,
  transport: PairingTransportMetadata | undefined,
  fallback?: BaseUrlSecurity,
): ServerTlsTrustInfo | undefined {
  if (!hasKnownTlsMetadata(securityRoot) && !hasKnownTlsMetadata(tlsRoot) && transport?.tlsEnabled === undefined) return undefined;
  const enabled =
    firstBoolean(tlsRoot, "enabled", "tls_enabled", "tlsEnabled", "https_enabled", "httpsEnabled") ??
    firstBoolean(securityRoot, "tls_enabled", "tlsEnabled", "https_enabled", "httpsEnabled", "backend_tls_enabled", "backendTlsEnabled") ??
    transport?.tlsEnabled ??
    fallback?.isHttps ??
    false;
  const statusRaw = (
    firstText(tlsRoot, "trust_status", "trustStatus", "status", "state") ??
    firstText(securityRoot, "trust_status", "trustStatus", "status", "state") ??
    ""
  ).toLowerCase();
  const normalizedStatus = statusRaw.replace(/[\s-]+/g, "_");
  const isSelfSigned = Boolean(
    firstBoolean(tlsRoot, "self_signed", "selfSigned", "is_self_signed", "isSelfSigned") ??
      firstBoolean(securityRoot, "self_signed", "selfSigned", "is_self_signed", "isSelfSigned") ??
      (normalizedStatus.includes("self_signed") || normalizedStatus.includes("selfsigned")),
  );
  const trusted =
    firstBoolean(tlsRoot, "trusted", "is_trusted", "isTrusted", "system_trusted", "systemTrusted") ??
    firstBoolean(securityRoot, "trusted", "is_trusted", "isTrusted", "system_trusted", "systemTrusted");
  const statusRequiresTrust =
    normalizedStatus.includes("requires_trust") ||
    normalizedStatus.includes("trust_required") ||
    normalizedStatus.includes("untrusted") ||
    normalizedStatus.includes("unknown_ca") ||
    normalizedStatus.includes("self_signed") ||
    normalizedStatus.includes("selfsigned");
  const requiresTrust = Boolean(
    firstBoolean(tlsRoot, "requires_trust", "requiresTrust", "trust_required", "trustRequired") ??
      firstBoolean(securityRoot, "requires_trust", "requiresTrust", "trust_required", "trustRequired") ??
      (statusRequiresTrust || (isSelfSigned && trusted !== true)),
  );
  const trustStatus: ServerTlsTrustStatus = !enabled
    ? "not_enabled"
    : requiresTrust
      ? "requires_trust"
      : trusted === true || normalizedStatus === "trusted" || normalizedStatus === "system_trusted" || normalizedStatus === "valid"
        ? "trusted"
        : normalizedStatus === "untrusted"
          ? "untrusted"
          : "unknown";
  const fingerprintSha256 = normalizeFingerprint(
    firstText(
      tlsRoot,
      "fingerprint_sha256",
      "fingerprintSha256",
      "sha256_fingerprint",
      "sha256Fingerprint",
      "certificate_fingerprint_sha256",
      "certificateFingerprintSha256",
      "fingerprint",
    ) ??
      firstText(
        securityRoot,
        "fingerprint_sha256",
        "fingerprintSha256",
        "sha256_fingerprint",
        "sha256Fingerprint",
        "certificate_fingerprint_sha256",
        "certificateFingerprintSha256",
        "fingerprint",
      ),
  );
  return {
    enabled,
    trustStatus,
    requiresTrust,
    isSelfSigned,
    ...(trusted !== undefined ? { trusted } : {}),
    ...(fingerprintSha256 ? { fingerprintSha256 } : {}),
    ...(firstText(tlsRoot, "subject") ?? firstText(securityRoot, "subject") ? { subject: firstText(tlsRoot, "subject") ?? firstText(securityRoot, "subject") } : {}),
    ...(firstText(tlsRoot, "issuer") ?? firstText(securityRoot, "issuer") ? { issuer: firstText(tlsRoot, "issuer") ?? firstText(securityRoot, "issuer") } : {}),
    ...(firstText(tlsRoot, "valid_from", "validFrom", "not_before", "notBefore") ?? firstText(securityRoot, "valid_from", "validFrom", "not_before", "notBefore")
      ? { validFrom: firstText(tlsRoot, "valid_from", "validFrom", "not_before", "notBefore") ?? firstText(securityRoot, "valid_from", "validFrom", "not_before", "notBefore") }
      : {}),
    ...(firstText(tlsRoot, "valid_to", "validTo", "not_after", "notAfter") ?? firstText(securityRoot, "valid_to", "validTo", "not_after", "notAfter")
      ? { validTo: firstText(tlsRoot, "valid_to", "validTo", "not_after", "notAfter") ?? firstText(securityRoot, "valid_to", "validTo", "not_after", "notAfter") }
      : {}),
    ...(requiresTrust ? { warning: SELF_SIGNED_TLS_WARNING } : {}),
  };
}

export function hasKnownTransportMetadata(value: Record<string, unknown> | undefined): boolean {
  return Boolean(
    value &&
      [
        "transport",
        "http_scheme",
        "httpScheme",
        "websocket_scheme",
        "webSocketScheme",
        "ws_scheme",
        "wsScheme",
        "scheme",
        "protocol",
        "tls_enabled",
        "tlsEnabled",
        "https_enabled",
        "httpsEnabled",
        "backend_tls_enabled",
        "backendTlsEnabled",
        "advertised_base_url",
        "advertisedBaseUrl",
        "server_url",
        "serverUrl",
        "server_origin",
        "serverOrigin",
        "origin",
      ].some((key) => key in value),
  );
}

export function hasKnownTlsMetadata(value: Record<string, unknown> | undefined): boolean {
  return Boolean(
    value &&
      [
        "tls",
        "certificate",
        "cert",
        "enabled",
        "trust_status",
        "trustStatus",
        "requires_trust",
        "requiresTrust",
        "trust_required",
        "trustRequired",
        "self_signed",
        "selfSigned",
        "is_self_signed",
        "isSelfSigned",
        "trusted",
        "is_trusted",
        "isTrusted",
        "system_trusted",
        "systemTrusted",
        "fingerprint_sha256",
        "fingerprintSha256",
        "sha256_fingerprint",
        "sha256Fingerprint",
        "certificate_fingerprint_sha256",
        "certificateFingerprintSha256",
        "fingerprint",
      ].some((key) => key in value),
  );
}

export function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

export function firstText(value: Record<string, unknown> | undefined, ...keys: string[]): string | undefined {
  if (!value) return undefined;
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
    if (typeof candidate === "number" || typeof candidate === "boolean") return String(candidate);
  }
  return undefined;
}

export function firstBoolean(value: Record<string, unknown> | undefined, ...keys: string[]): boolean | undefined {
  if (!value) return undefined;
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "boolean") return candidate;
    if (typeof candidate === "string") {
      const normalized = candidate.trim().toLowerCase();
      if (["true", "1", "yes", "enabled", "on"].includes(normalized)) return true;
      if (["false", "0", "no", "disabled", "off"].includes(normalized)) return false;
    }
    if (typeof candidate === "number") return candidate !== 0;
  }
  return undefined;
}

export function normalizeHttpScheme(value: string | undefined): "http" | "https" | string | undefined {
  const normalized = value?.trim().toLowerCase().replace(/:$/, "");
  if (!normalized) return undefined;
  if (normalized === "http" || normalized === "https") return normalized;
  return normalized;
}

export function normalizeWebSocketScheme(value: string | undefined): "ws" | "wss" | string | undefined {
  const normalized = value?.trim().toLowerCase().replace(/:$/, "");
  if (!normalized) return undefined;
  if (normalized === "ws" || normalized === "wss") return normalized;
  return normalized;
}

export function normalizeFingerprint(value: string | undefined): string {
  return value?.trim().replace(/\s+/g, "").toUpperCase() ?? "";
}

export function isLoopbackHostname(hostname: string): boolean {
  return hostname === "localhost" || hostname === "::1" || hostname === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(hostname);
}

export function pairingSessionExpiresAt(payload: Pick<PairResult, "expires_in" | "expires_at" | "expiresAt">): string | undefined {
  const explicit = typeof payload.expires_at === "string" ? payload.expires_at : typeof payload.expiresAt === "string" ? payload.expiresAt : "";
  const explicitMs = Date.parse(explicit);
  if (Number.isFinite(explicitMs)) return new Date(explicitMs).toISOString();
  if (!Number.isFinite(payload.expires_in)) return undefined;
  if (payload.expires_in <= 0) return new Date(0).toISOString();
  return new Date(Date.now() + payload.expires_in * 1000).toISOString();
}

export function pairingRefreshExpiresAt(
  payload: Pick<PairResult, "refresh_expires_in" | "refresh_expires_at">,
): string | undefined {
  const explicit = typeof payload.refresh_expires_at === "string" ? payload.refresh_expires_at : "";
  const explicitMs = Date.parse(explicit);
  if (Number.isFinite(explicitMs)) return new Date(explicitMs).toISOString();
  if (!Number.isFinite(payload.refresh_expires_in)) return undefined;
  if (payload.refresh_expires_in <= 0) return new Date(0).toISOString();
  return new Date(Date.now() + payload.refresh_expires_in * 1000).toISOString();
}

export function sessionHasUnsafeRemoteTransport(security: BaseUrlSecurity): boolean {
  return !security.isLoopback && (!security.backendTlsEnabled || security.webSocketProtocol !== "wss:");
}

export function validatePairResult(payload: unknown): { payload: PairResult; expiresAt: string; refreshExpiresAt: string } {
  const record = asRecord(payload);
  if (!record) {
    throw invalidPairingResponse("Pairing response must be a JSON object.");
  }

  const token = typeof record.token === "string" ? record.token : "";
  const tokenType = typeof record.token_type === "string" ? record.token_type : "";
  const deviceId = typeof record.device_id === "string" ? record.device_id : "";
  const refreshToken = typeof record.refresh_token === "string" ? record.refresh_token : "";
  const tokenFamilyId = typeof record.token_family_id === "string" ? record.token_family_id : "";
  const deviceCredentialId = typeof record.device_credential_id === "string" ? record.device_credential_id : "";
  if (!token || tokenType !== "Bearer" || !deviceId || !refreshToken || !tokenFamilyId || !deviceCredentialId) {
    throw invalidPairingResponse("Pairing response is missing its access token, refresh family, or device binding.");
  }
  if (!isWebSocketSubprotocolToken(token)) {
    throw invalidPairingResponse("Pairing response token cannot be used as a WebSocket subprotocol.");
  }

  const expiresAt = pairingSessionExpiresAt(payload as Pick<PairResult, "expires_in" | "expires_at" | "expiresAt">);
  if (!expiresAt) {
    throw invalidPairingResponse("Pairing response is missing a usable token expiry.");
  }
  if (isExpiredTimestamp(expiresAt, Date.now() + SESSION_TOKEN_EXPIRY_SKEW_MS)) {
    throw new AuthExpiredError("Pairing token is already expired. Generate a new pairing code.");
  }
  const refreshExpiresAt = pairingRefreshExpiresAt(
    payload as Pick<PairResult, "refresh_expires_in" | "refresh_expires_at">,
  );
  if (!refreshExpiresAt) {
    throw invalidPairingResponse("Pairing response is missing a usable refresh-token expiry.");
  }
  if (isExpiredTimestamp(refreshExpiresAt, Date.now() + SESSION_TOKEN_EXPIRY_SKEW_MS)) {
    throw new AuthExpiredError("Pairing refresh token is already expired. Generate a new pairing code.");
  }
  assertRefreshTokenFormat(refreshToken);
  return { payload: payload as PairResult, expiresAt, refreshExpiresAt };
}

export function assertUsablePairingToken(session: PairingSession): void {
  if (!session.token?.trim()) {
    throw new AuthExpiredError("Mobile session token is missing. Pair this phone again.");
  }
  assertWebSocketSubprotocolToken(session.token);
  if (isExpiredTimestamp(session.expiresAt, Date.now() + SESSION_TOKEN_EXPIRY_SKEW_MS)) {
    throw new AuthExpiredError("Mobile session token has expired. Pair this phone again.");
  }
}

export function assertUsableRefreshToken(session: PairingSession): void {
  assertRefreshTokenFormat(session.refreshToken);
  if (isExpiredTimestamp(session.refreshExpiresAt, Date.now() + SESSION_TOKEN_EXPIRY_SKEW_MS)) {
    throw new AuthExpiredError("Mobile refresh token has expired. Pair this phone again.");
  }
}

function assertRefreshTokenFormat(refreshToken: string): void {
  if (!/^lrt\.mrt_[a-f0-9]{32}\.[A-Za-z0-9_-]{32,}$/.test(refreshToken)) {
    throw new AuthExpiredError("Mobile refresh token is missing or invalid. Pair this phone again.");
  }
}

export function isExpiredTimestamp(value: string | undefined, nowMs = Date.now()): boolean {
  if (!value) return false;
  const expiresAt = Date.parse(value);
  return !Number.isFinite(expiresAt) || expiresAt <= nowMs;
}

export function assertWebSocketSubprotocolToken(token: string): void {
  if (!isWebSocketSubprotocolToken(token)) {
    throw new ForbiddenError("WebSocket tokens must be sent as valid Sec-WebSocket-Protocol tokens.");
  }
}

export function isWebSocketSubprotocolToken(token: string): boolean {
  return Boolean(token && token.trim() === token && WEB_SOCKET_SUBPROTOCOL_TOKEN_PATTERN.test(token));
}

// Smoke tests load this module via vm.runInNewContext; array literals created in
// that sandbox fail assert.deepEqual against host-context arrays even when contents
// match. URLSearchParams#getAll returns a host Array we can push into.
export function webSocketProtocolList(protocol: string): string[] {
  const protocols = new URL("https://lengrvis.invalid").searchParams.getAll("protocol");
  protocols.push(protocol);
  return protocols;
}
