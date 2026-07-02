import { shell } from "electron";
import { lookup } from "node:dns/promises";
import { BlockList, isIP } from "node:net";

const ALLOWED_EXTERNAL_PROTOCOLS = new Set(["https:", "mailto:"]);
const EXTERNAL_URL_MAX_CHARS = 2048;
const MAILTO_DENIED_QUERY_KEYS = new Set(["bcc", "body", "cc"]);
const EXTERNAL_BLOCKED_ADDRESSES = createExternalBlockedAddressList();

export async function openSafeExternalUrl(rawUrl: string): Promise<void> {
  const parsed = validateSafeExternalUrl(rawUrl);
  if (parsed.protocol === "https:" && (await externalHostnameResolvesToBlockedAddress(parsed.hostname))) {
    throw new Error("External URL host resolved to a blocked address");
  }
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
  const ipv4Mapped = normalized.match(/^::ffff:(?:(\d{1,3}(?:\.\d{1,3}){3})|([0-9a-f]{1,4}):([0-9a-f]{1,4}))$/i);
  if (ipv4Mapped) {
    const mappedIpv4 = ipv4Mapped[1] ?? ipv4FromHexWords(ipv4Mapped[2] ?? "", ipv4Mapped[3] ?? "");
    return isBlockedExternalIpAddress(mappedIpv4);
  }
  return isBlockedExternalIpAddress(normalized);
}

async function externalHostnameResolvesToBlockedAddress(hostname: string): Promise<boolean> {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!normalized || isBlockedExternalHost(normalized) || isIP(normalized)) {
    return false;
  }
  try {
    const addresses = await lookup(normalized, { all: true, verbatim: false });
    return addresses.length === 0 || addresses.some((item) => isBlockedExternalHost(item.address));
  } catch {
    return true;
  }
}

function isBlockedExternalIpAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) {
    return EXTERNAL_BLOCKED_ADDRESSES.check(address, "ipv4");
  }
  if (family === 6) {
    return EXTERNAL_BLOCKED_ADDRESSES.check(address, "ipv6");
  }
  return false;
}

function createExternalBlockedAddressList(): BlockList {
  const blockList = new BlockList();
  blockList.addSubnet("0.0.0.0", 8, "ipv4");
  blockList.addSubnet("10.0.0.0", 8, "ipv4");
  blockList.addSubnet("100.64.0.0", 10, "ipv4");
  blockList.addSubnet("127.0.0.0", 8, "ipv4");
  blockList.addSubnet("169.254.0.0", 16, "ipv4");
  blockList.addSubnet("172.16.0.0", 12, "ipv4");
  blockList.addSubnet("192.168.0.0", 16, "ipv4");
  blockList.addSubnet("198.18.0.0", 15, "ipv4");
  blockList.addAddress("::", "ipv6");
  blockList.addAddress("::1", "ipv6");
  blockList.addSubnet("fc00::", 7, "ipv6");
  blockList.addSubnet("fe80::", 10, "ipv6");
  return blockList;
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
