import { describe, expect, it, vi } from "vitest";
vi.mock("electron", () => ({ shell: { openExternal: vi.fn() } }));
import { isSafeExternalUrl, openSafeExternalUrl } from "./externalUrl";
import { shell } from "electron";

describe("external mailto validation", () => {
  it.each(["%0D", "%0A", "%00", "%7F", "%09"])("rejects encoded recipient control %s before shell dispatch", async (control) => {
    const url = `mailto:person@example.com${control}Bcc:other@example.com`;
    expect(isSafeExternalUrl(url)).toBe(false);
    await expect(openSafeExternalUrl(url)).rejects.toThrow("unsafe recipient");
    expect(shell.openExternal).not.toHaveBeenCalled();
  });

  it("continues to allow ordinary percent-encoded recipients and subjects", () => {
    expect(isSafeExternalUrl("mailto:person%2Bsupport@example.com?subject=Hello%20there")).toBe(true);
    expect(isSafeExternalUrl("mailto:person@example.com?subject=hello%0D%0ABcc:other@example.com")).toBe(false);
    expect(isSafeExternalUrl("mailto:person@example.com?bcc=other@example.com")).toBe(false);
  });
});
