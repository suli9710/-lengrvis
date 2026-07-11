import { afterEach, describe, expect, it } from "vitest";

import {
  assertBrowserHostUrlAllowed,
  isBlockedBrowserHostHostname,
  isBlockedBrowserHostNavigation,
  isBrowserHostRequestAllowed,
  resolveBrowserHostPinnedAddress
} from "./browserHostNetworkGuard";

const PRIVATE_NETWORK_ENV = "LENGRVIS_BROWSER_HOST_ALLOW_PRIVATE_NETWORK";

describe("browserHostNetworkGuard", () => {
  afterEach(() => {
    delete process.env[PRIVATE_NETWORK_ENV];
  });

  it("blocks non-http navigation and private hostnames by default", () => {
    expect(isBlockedBrowserHostNavigation("about:blank")).toBe(false);
    expect(isBlockedBrowserHostNavigation("file:///C:/Users/Suli/secret.txt")).toBe(true);
    expect(isBlockedBrowserHostNavigation("https://127.0.0.1:8000")).toBe(true);
    expect(isBlockedBrowserHostNavigation("https://example.test")).toBe(false);
  });

  it("detects localhost, metadata, private ranges, and IPv4-mapped IPv6 hostnames", () => {
    expect(isBlockedBrowserHostHostname("localhost")).toBe(true);
    expect(isBlockedBrowserHostHostname("app.localhost")).toBe(true);
    expect(isBlockedBrowserHostHostname("metadata.google.internal")).toBe(true);
    expect(isBlockedBrowserHostHostname("10.10.10.10")).toBe(true);
    expect(isBlockedBrowserHostHostname("192.0.2.1")).toBe(true);
    expect(isBlockedBrowserHostHostname("198.51.100.1")).toBe(true);
    expect(isBlockedBrowserHostHostname("224.0.0.1")).toBe(true);
    expect(isBlockedBrowserHostHostname("240.0.0.1")).toBe(true);
    expect(isBlockedBrowserHostHostname("ff02::1")).toBe(true);
    expect(isBlockedBrowserHostHostname("::ffff:127.0.0.1")).toBe(true);
    expect(isBlockedBrowserHostHostname("93.184.216.34")).toBe(false);
  });

  it("fails closed when DNS resolution is empty, private, or unavailable", async () => {
    await expect(
      isBrowserHostRequestAllowed("https://safe-name.example.test", async () => [{ address: "10.0.0.5" }])
    ).resolves.toBe(false);
    await expect(isBrowserHostRequestAllowed("https://empty.example.test", async () => [])).resolves.toBe(false);
    await expect(
      isBrowserHostRequestAllowed("https://dns-error.example.test", async () => {
        throw new Error("dns unavailable");
      })
    ).resolves.toBe(false);
  });

  it("allows public DNS targets and about:blank request probes", async () => {
    await expect(isBrowserHostRequestAllowed("about:blank")).resolves.toBe(true);
    await expect(
      isBrowserHostRequestAllowed("https://example.test", async () => [{ address: "93.184.216.34" }])
    ).resolves.toBe(true);
  });

  it("pins only literal IP answers so a malformed resolver result cannot trigger a second DNS lookup", async () => {
    await expect(
      resolveBrowserHostPinnedAddress("example.test", async () => [{ address: "rebinding.invalid" }])
    ).rejects.toThrow(/valid IP address/);
    await expect(
      resolveBrowserHostPinnedAddress("example.test", async () => [
        { address: "93.184.216.34" },
        { address: "rebinding.invalid" }
      ])
    ).rejects.toThrow(/valid IP address/);
  });

  it("allows private hostnames only with the explicit development override", async () => {
    expect(() => assertBrowserHostUrlAllowed(new URL("https://127.0.0.1:8000"))).toThrow(/blocks localhost/);

    process.env[PRIVATE_NETWORK_ENV] = "1";

    expect(() => assertBrowserHostUrlAllowed(new URL("https://127.0.0.1:8000"))).not.toThrow();
    await expect(isBrowserHostRequestAllowed("https://127.0.0.1:8000")).resolves.toBe(true);
    await expect(isBrowserHostRequestAllowed("file:///C:/Users/Suli/secret.txt")).resolves.toBe(false);
  });
});
