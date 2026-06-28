export interface DesktopMobilePairingCode {
  code: string;
  claim_secret?: string;
  expires_at: string;
  expires_in: number;
  server: {
    host: string;
    port: number;
    scheme?: string;
    origin?: string;
    transport_security?: Record<string, unknown>;
  };
  server_origin?: string;
  transport_security?: Record<string, unknown>;
  https_enabled?: boolean;
  trust_required?: boolean;
}

export interface MobilePairingPayloadV1 {
  type: "lengrvis.mobile_pairing";
  version: 1;
  base_url: string;
  code: string;
  claim_secret: string;
  expires_at: string;
  expires_in: number;
  server: {
    host: string;
    port: number;
    scheme: "http" | "https";
    origin: string;
    transport_security?: Record<string, unknown>;
  };
  transport_security?: Record<string, unknown>;
  https_enabled?: boolean;
  trust_required?: boolean;
}

export interface MobilePairingQrContent {
  type: "lengrvis.mobile_pairing.qr";
  version: 1;
  value: string;
  mime_type: "application/json";
  encoding: "utf-8";
  length: number;
  payload: MobilePairingPayloadV1;
}

export function buildMobilePairingPayload(pairing: DesktopMobilePairingCode): MobilePairingPayloadV1 {
  const baseUrl = formatMobilePairingBaseUrl(pairing);
  const parsed = new URL(baseUrl);
  const scheme = parsed.protocol === "https:" ? "https" : "http";
  const host = pairing.server.host || parsed.hostname;
  const port = Number(pairing.server.port || parsed.port || (scheme === "https" ? 443 : 80));
  const transportSecurity = pairing.transport_security ?? pairing.server.transport_security;

  return {
    type: "lengrvis.mobile_pairing",
    version: 1,
    base_url: baseUrl,
    code: pairing.code,
    claim_secret: pairing.claim_secret ?? "",
    expires_at: pairing.expires_at,
    expires_in: pairing.expires_in,
    server: {
      host,
      port,
      scheme,
      origin: baseUrl,
      ...(transportSecurity ? { transport_security: transportSecurity } : {})
    },
    ...(transportSecurity ? { transport_security: transportSecurity } : {}),
    ...(typeof pairing.https_enabled === "boolean" ? { https_enabled: pairing.https_enabled } : {}),
    ...(typeof pairing.trust_required === "boolean" ? { trust_required: pairing.trust_required } : {})
  };
}

export function serializeMobilePairingPayload(pairing: DesktopMobilePairingCode): string {
  return JSON.stringify(buildMobilePairingPayload(pairing));
}

export function buildMobilePairingQrContent(pairing: DesktopMobilePairingCode): MobilePairingQrContent {
  const payload = buildMobilePairingPayload(pairing);
  const value = JSON.stringify(payload);
  return {
    type: "lengrvis.mobile_pairing.qr",
    version: 1,
    value,
    mime_type: "application/json",
    encoding: "utf-8",
    length: value.length,
    payload
  };
}

export function serializeMobilePairingQrContent(pairing: DesktopMobilePairingCode): string {
  return buildMobilePairingQrContent(pairing).value;
}

export function formatMobilePairingBaseUrl(pairing: DesktopMobilePairingCode): string {
  const origin = pairing.server.origin || pairing.server_origin;
  if (origin) {
    return normalizeHttpOrigin(origin);
  }
  const scheme = pairing.server.scheme || (pairing.https_enabled ? "https" : "http");
  return normalizeHttpOrigin(`${scheme}://${pairing.server.host}:${pairing.server.port}`);
}

function normalizeHttpOrigin(value: string): string {
  const parsed = new URL(value);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("Mobile pairing payload requires an HTTP(S) server URL.");
  }
  return parsed.origin;
}
