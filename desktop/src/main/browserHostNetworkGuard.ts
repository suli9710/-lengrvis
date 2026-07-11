import { lookup } from "node:dns/promises";
import { BlockList, isIP } from "node:net";

const BROWSER_HOST_ALLOW_PRIVATE_NETWORK_ENV = "LENGRVIS_BROWSER_HOST_ALLOW_PRIVATE_NETWORK";
const BROWSER_HOST_BLOCKED_ADDRESSES = createBrowserHostBlockedAddressList();

export type BrowserHostDnsLookup = (hostname: string) => Promise<Array<{ address: string }>>;

export async function resolveBrowserHostPinnedAddress(
  hostname: string,
  lookupHost: BrowserHostDnsLookup = defaultBrowserHostDnsLookup
): Promise<string> {
  const normalized = normalizeBrowserHostHostname(hostname);
  if (!normalized || isBlockedBrowserHostHostname(normalized)) {
    if (!browserHostPrivateNetworkAllowed()) {
      throw new Error("BrowserHost blocks localhost, private network, link-local, and metadata URLs by default");
    }
  }
  if (isIP(normalized)) {
    return normalized;
  }
  const addresses = normalizeBrowserHostDnsAnswers(await lookupHost(normalized));
  if (!browserHostPrivateNetworkAllowed() && addresses.some((address) => isBlockedBrowserHostHostname(address))) {
    throw new Error("BrowserHost target resolved to a blocked network address");
  }
  return addresses[0];
}

export function isBlockedBrowserHostNavigation(value: string): boolean {
  try {
    if (value === "about:blank") return false;
    const parsed = new URL(value);
    if (!["https:", "http:"].includes(parsed.protocol)) {
      return true;
    }
    assertBrowserHostUrlAllowed(parsed);
    return false;
  } catch {
    return true;
  }
}

export function assertBrowserHostUrlAllowed(parsed: URL): void {
  if (browserHostPrivateNetworkAllowed()) {
    return;
  }
  if (isBlockedBrowserHostHostname(parsed.hostname)) {
    throw new Error("BrowserHost blocks localhost, private network, link-local, and metadata URLs by default");
  }
}

export async function isBrowserHostRequestAllowed(
  value: string,
  lookupHost: BrowserHostDnsLookup = defaultBrowserHostDnsLookup
): Promise<boolean> {
  try {
    if (value === "about:blank") return true;
    const parsed = new URL(value);
    if (parsed.protocol === "file:") {
      return false;
    }
    if (!parsed.hostname) {
      return true;
    }
    if (browserHostPrivateNetworkAllowed()) {
      return true;
    }
    if (isBlockedBrowserHostHostname(parsed.hostname)) {
      return false;
    }
    return !(await browserHostHostnameResolvesToBlockedAddress(parsed.hostname, lookupHost));
  } catch {
    return false;
  }
}

export function isBlockedBrowserHostHostname(hostname: string): boolean {
  const normalized = normalizeBrowserHostHostname(hostname);
  if (normalized === "localhost" || normalized.endsWith(".localhost") || normalized === "metadata.google.internal") {
    return true;
  }
  const mappedIpv4 = browserHostIpv4MappedAddress(normalized);
  if (mappedIpv4) {
    return isBlockedBrowserHostIpAddress(mappedIpv4);
  }
  return isBlockedBrowserHostIpAddress(normalized);
}

export function browserHostPrivateNetworkAllowed(): boolean {
  return process.env[BROWSER_HOST_ALLOW_PRIVATE_NETWORK_ENV] === "1";
}

async function browserHostHostnameResolvesToBlockedAddress(
  hostname: string,
  lookupHost: BrowserHostDnsLookup
): Promise<boolean> {
  const normalized = normalizeBrowserHostHostname(hostname);
  if (!normalized || isBlockedBrowserHostHostname(normalized) || isIP(normalized)) {
    return false;
  }
  try {
    const addresses = normalizeBrowserHostDnsAnswers(await lookupHost(normalized));
    return addresses.some((address) => isBlockedBrowserHostHostname(address));
  } catch {
    return true;
  }
}

function normalizeBrowserHostDnsAnswers(addresses: Array<{ address: string }>): string[] {
  if (addresses.length === 0) {
    throw new Error("BrowserHost target did not resolve to an address");
  }
  const normalized = addresses.map((item) => normalizeBrowserHostHostname(item.address));
  if (normalized.some((address) => isIP(address) === 0)) {
    throw new Error("BrowserHost DNS resolver did not return a valid IP address");
  }
  return normalized;
}

async function defaultBrowserHostDnsLookup(hostname: string): Promise<Array<{ address: string }>> {
  return lookup(hostname, { all: true, verbatim: false });
}

function normalizeBrowserHostHostname(hostname: string): string {
  return hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
}

function isBlockedBrowserHostIpAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) {
    return BROWSER_HOST_BLOCKED_ADDRESSES.check(address, "ipv4");
  }
  if (family === 6) {
    return BROWSER_HOST_BLOCKED_ADDRESSES.check(address, "ipv6");
  }
  return false;
}

function browserHostIpv4MappedAddress(hostname: string): string {
  const ipv4Mapped = hostname.match(/^::ffff:(?:(\d{1,3}(?:\.\d{1,3}){3})|([0-9a-f]{1,4}):([0-9a-f]{1,4}))$/i);
  if (!ipv4Mapped) {
    return "";
  }
  return ipv4Mapped[1] ?? ipv4FromHexWords(ipv4Mapped[2] ?? "", ipv4Mapped[3] ?? "");
}

function createBrowserHostBlockedAddressList(): BlockList {
  const blockList = new BlockList();
  blockList.addSubnet("0.0.0.0", 8, "ipv4");
  blockList.addSubnet("10.0.0.0", 8, "ipv4");
  blockList.addSubnet("100.64.0.0", 10, "ipv4");
  blockList.addSubnet("127.0.0.0", 8, "ipv4");
  blockList.addSubnet("169.254.0.0", 16, "ipv4");
  blockList.addSubnet("172.16.0.0", 12, "ipv4");
  blockList.addSubnet("192.0.0.0", 24, "ipv4");
  blockList.addSubnet("192.0.2.0", 24, "ipv4");
  blockList.addSubnet("192.168.0.0", 16, "ipv4");
  blockList.addSubnet("198.18.0.0", 15, "ipv4");
  blockList.addSubnet("198.51.100.0", 24, "ipv4");
  blockList.addSubnet("203.0.113.0", 24, "ipv4");
  blockList.addSubnet("224.0.0.0", 4, "ipv4");
  blockList.addSubnet("240.0.0.0", 4, "ipv4");
  blockList.addAddress("::", "ipv6");
  blockList.addAddress("::1", "ipv6");
  blockList.addSubnet("100::", 64, "ipv6");
  blockList.addSubnet("2001:db8::", 32, "ipv6");
  blockList.addSubnet("fc00::", 7, "ipv6");
  blockList.addSubnet("fe80::", 10, "ipv6");
  blockList.addSubnet("ff00::", 8, "ipv6");
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
