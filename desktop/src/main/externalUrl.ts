import { shell } from "electron";

const ALLOWED_EXTERNAL_PROTOCOLS = new Set(["https:", "mailto:"]);
const EXTERNAL_URL_MAX_CHARS = 2048;
const MAILTO_DENIED_QUERY_KEYS = new Set(["bcc", "body", "cc"]);

export async function openSafeExternalUrl(rawUrl: string): Promise<void> {
  const parsed = validateSafeExternalUrl(rawUrl);
  await shell.openExternal(parsed.toString());
}

export function isSafeExternalUrl(url: string): boolean {
  try {
    validateSafeExternalUrl(url);
    return true;
  } catch {
    return false;
  }
}

function validateSafeExternalUrl(rawUrl: string): URL {
  if (typeof rawUrl !== "string" || !rawUrl.trim() || rawUrl.length > EXTERNAL_URL_MAX_CHARS) {
    throw new Error("External URL is invalid");
  }
  if (/[\u0000-\u001F\u007F]/.test(rawUrl)) {
    throw new Error("External URL must not contain control characters");
  }
  const parsed = new URL(rawUrl);
  if (!ALLOWED_EXTERNAL_PROTOCOLS.has(parsed.protocol)) {
    throw new Error("External URL protocol is not allowed");
  }
  if (parsed.protocol === "mailto:") {
    for (const [key, value] of parsed.searchParams.entries()) {
      if (hasControlCharacters(key) || hasControlCharacters(value)) {
        throw new Error("External mailto URL contains unsafe header characters");
      }
      if (MAILTO_DENIED_QUERY_KEYS.has(key.toLowerCase())) {
        throw new Error("External mailto URL contains unsafe message fields");
      }
    }
    return parsed;
  }
  if (parsed.username || parsed.password) {
    throw new Error("External URL credentials are not allowed");
  }
  if (!parsed.hostname || isBlockedExternalHost(parsed.hostname)) {
    throw new Error("External URL host is not allowed");
  }
  return parsed;
}

function isBlockedExternalHost(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (normalized === "localhost" || normalized.endsWith(".localhost")) {
    return true;
  }
  if (normalized === "::1" || normalized === "0:0:0:0:0:0:0:1") {
    return true;
  }
  const ipv4Mapped = normalized.match(/^::ffff:(?:(\d{1,3}(?:\.\d{1,3}){3})|([0-9a-f]{1,4}):([0-9a-f]{1,4}))$/i);
  if (ipv4Mapped) {
    const mappedIpv4 = ipv4Mapped[1] ?? ipv4FromHexWords(ipv4Mapped[2] ?? "", ipv4Mapped[3] ?? "");
    return isBlockedIpv4Host(mappedIpv4);
  }
  if (normalized.startsWith("fe80:") || normalized.startsWith("fc") || normalized.startsWith("fd")) {
    return true;
  }
  return isBlockedIpv4Host(normalized);
}

function isBlockedIpv4Host(hostname: string): boolean {
  const octets = hostname.split(".").map((part) => Number(part));
  if (octets.length !== 4 || octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return false;
  }
  const [first = 0, second = 0] = octets;
  return (
    first === 0 ||
    first === 10 ||
    first === 127 ||
    first === 169 && second === 254 ||
    first === 172 && second >= 16 && second <= 31 ||
    first === 192 && second === 168
  );
}

function ipv4FromHexWords(highWord: string, lowWord: string): string {
  const high = Number.parseInt(highWord, 16);
  const low = Number.parseInt(lowWord, 16);
  if (
    !Number.isInteger(high) ||
    !Number.isInteger(low) ||
    high < 0 ||
    high > 0xffff ||
    low < 0 ||
    low > 0xffff
  ) {
    return "";
  }
  return `${(high >> 8) & 0xff}.${high & 0xff}.${(low >> 8) & 0xff}.${low & 0xff}`;
}

function hasControlCharacters(value: string): boolean {
  return /[\u0000-\u001F\u007F]/.test(value);
}
