import { afterEach, describe, expect, it, vi } from "vitest";

import {
  revealFileEndpoint,
  showItemInFolderEndpoint,
  type FileLibraryEndpointRequest
} from "./fileLibraryClient";

describe("file library client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("uses the backend adapter when the Electron shell method is absent", async () => {
    (window as unknown as { lengrvis?: { shell: Record<string, never> } }).lengrvis = { shell: {} };
    const request = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      data: { ok: true, path: "C:\\Docs\\plan.md", revealed: true },
      receivedAt: "2026-07-04T00:00:00Z"
    });
    const endpointRequest = request as FileLibraryEndpointRequest;

    const reveal = await revealFileEndpoint(endpointRequest, "C:\\Docs\\plan.md");
    const show = await showItemInFolderEndpoint(endpointRequest, "C:\\Docs\\plan.md");

    expect(request).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/apps/reveal",
      method: "POST",
      body: { path: "C:\\Docs\\plan.md" },
      timeoutMs: 10_000
    });
    expect(reveal.data).toMatchObject({ ok: true, revealed: true, shown: true });
    expect(show.data).toMatchObject({ ok: true, revealed: true, shown: true });
  });

  it("prefers the Electron shell adapter", async () => {
    const showItemInFolder = vi.fn().mockResolvedValue({
      ok: true,
      path: "C:\\Docs\\plan.md",
      revealed: true,
      shown: true
    });
    (window as unknown as { lengrvis?: { shell: { showItemInFolder: typeof showItemInFolder } } }).lengrvis = {
      shell: { showItemInFolder }
    };
    const request = vi.fn();

    const response = await revealFileEndpoint(
      request as FileLibraryEndpointRequest,
      "C:\\Docs\\plan.md"
    );

    expect(request).not.toHaveBeenCalled();
    expect(showItemInFolder).toHaveBeenCalledWith("C:\\Docs\\plan.md");
    expect(response).toMatchObject({ ok: true, status: 200, data: { shown: true } });
  });
});
