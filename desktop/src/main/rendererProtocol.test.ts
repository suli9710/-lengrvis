import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

const electronProtocolMocks = vi.hoisted(() => ({
  handle: vi.fn(),
  netFetch: vi.fn(),
  registerSchemesAsPrivileged: vi.fn()
}));

vi.mock("electron", () => ({
  net: { fetch: electronProtocolMocks.netFetch },
  protocol: {
    handle: electronProtocolMocks.handle,
    registerSchemesAsPrivileged: electronProtocolMocks.registerSchemesAsPrivileged
  }
}));

import {
  handleRendererProtocolRequest,
  isPackagedRendererEntryUrl,
  registerPackagedRendererProtocol,
  registerRendererSchemePrivileges,
  resolveRendererAssetPath
} from "./rendererProtocol";

const temporaryDirectories: string[] = [];

function createTemporaryDirectory(prefix: string): string {
  const directory = mkdtempSync(join(tmpdir(), prefix));
  temporaryDirectories.push(directory);
  return directory;
}

afterEach(() => {
  vi.clearAllMocks();
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { force: true, recursive: true });
  }
});

describe("packaged renderer protocol", () => {
  it("registers only the minimum renderer scheme privileges before app readiness", () => {
    registerRendererSchemePrivileges();

    expect(electronProtocolMocks.registerSchemesAsPrivileged).toHaveBeenCalledOnce();
    expect(electronProtocolMocks.registerSchemesAsPrivileged).toHaveBeenCalledWith([
      {
        scheme: "app",
        privileges: {
          secure: true,
          standard: true,
          supportFetchAPI: true
        }
      }
    ]);
  });

  it("registers the packaged handler and serves from the canonical renderer root", async () => {
    const root = createTemporaryDirectory("lengrvis-renderer-registration-");
    writeFileSync(join(root, "index.html"), "<main>registered</main>", "utf8");
    electronProtocolMocks.netFetch.mockResolvedValue(
      new Response("<main>registered</main>", { status: 200 })
    );

    registerPackagedRendererProtocol(root);

    expect(electronProtocolMocks.handle).toHaveBeenCalledOnce();
    expect(electronProtocolMocks.handle).toHaveBeenCalledWith("app", expect.any(Function));
    const handler = electronProtocolMocks.handle.mock.calls[0][1] as (
      request: Request
    ) => Promise<Response>;
    const response = await handler(new Request("app://local/index.html"));
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("<main>registered</main>");
    expect(electronProtocolMocks.netFetch).toHaveBeenCalledOnce();
  });

  it("resolves only exact-host files inside the renderer root", () => {
    const root = join("D:\\app", "renderer");

    expect(resolveRendererAssetPath(root, "app://local/")).toBe(join(root, "index.html"));
    expect(resolveRendererAssetPath(root, "app://local/assets/app-123.js")).toBe(
      join(root, "assets", "app-123.js")
    );
    expect(resolveRendererAssetPath(root, "app://local/index.html?view=settings#privacy")).toBe(
      join(root, "index.html")
    );
    expect(resolveRendererAssetPath(root, "app://evil/index.html")).toBeNull();
    expect(resolveRendererAssetPath(root, "app://user@local/index.html")).toBeNull();
    expect(resolveRendererAssetPath(root, "app://local:443/index.html")).toBeNull();
  });

  it.each([
    "app://local/../secret.txt",
    "app://local/%2e%2e/secret.txt",
    "app://local/%252e%252e/secret.txt",
    "app://local/%5c..%5csecret.txt",
    "app://local/C%3a/Windows/win.ini",
    "app://local/assets//app.js",
    "app://local/assets/app.js%3asecret",
    "app://local/assets/app.js%00.png",
    "app://local/assets/app.js%ZZ",
    "app://local/NUL.js",
    "app://local/assets/con.txt",
    "app://local/assets/COM1.css",
    "app://local/assets/COM¹.js",
    "app://local/assets/COM².js",
    "app://local/assets/COM³.js",
    "app://local/assets/LPT¹.css",
    "app://local/assets/LPT².css",
    "app://local/assets/LPT³.css",
    "app://lo\tcal/index.html",
    "app://local\n/index.html"
  ])("rejects ambiguous or escaping path %s", (url) => {
    expect(resolveRendererAssetPath("D:\\app\\renderer", url)).toBeNull();
  });

  it("serves files with restrictive response headers and supports HEAD", async () => {
    const root = createTemporaryDirectory("lengrvis-renderer-protocol-");
    writeFileSync(join(root, "index.html"), "<main>Lengrvis</main>", "utf8");
    const fetchCalls: string[] = [];
    const fetchFile = async (url: string) => {
      fetchCalls.push(url);
      return new Response("<main>Lengrvis</main>", {
        headers: { "Content-Type": "application/octet-stream" }
      });
    };

    const response = await handleRendererProtocolRequest(
      realpathSync(root),
      { method: "GET", url: "app://local/index.html" },
      fetchFile
    );
    const head = await handleRendererProtocolRequest(
      realpathSync(root),
      { method: "HEAD", url: "app://local/index.html" },
      fetchFile
    );

    expect(response.status).toBe(200);
    expect(await response.text()).toBe("<main>Lengrvis</main>");
    expect(response.headers.get("content-type")).toBe("text/html; charset=utf-8");
    expect(response.headers.get("content-security-policy")).toContain("default-src 'self'");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(head.status).toBe(200);
    expect(await head.text()).toBe("");
    expect(fetchCalls).toHaveLength(2);
  });

  it("does not follow an in-root symlink or junction outside the renderer root", async (context) => {
    const parent = createTemporaryDirectory("lengrvis-renderer-symlink-");
    const root = join(parent, "renderer");
    const outside = join(parent, "outside");
    mkdirSync(root);
    mkdirSync(outside);
    writeFileSync(join(outside, "escape.js"), "private", "utf8");
    try {
      symlinkSync(
        outside,
        join(root, "linked"),
        process.platform === "win32" ? "junction" : "dir"
      );
    } catch (error) { // broad-exception-boundary: skip known host capability errors and rethrow all others.
      const code = String((error as NodeJS.ErrnoException)?.code ?? "");
      if (["EACCES", "EPERM", "UNKNOWN"].includes(code)) {
        context.skip(`symlink creation is unavailable on this host (${code})`);
      }
      throw error;
    }

    const response = await handleRendererProtocolRequest(
      realpathSync(root),
      { method: "GET", url: "app://local/linked/escape.js" },
      async () => new Response("private")
    );

    expect(response.status).toBe(404);
  });

  it("rejects directories, secondary HTML documents, and unknown file types", async () => {
    const root = createTemporaryDirectory("lengrvis-renderer-types-");
    mkdirSync(join(root, "assets"));
    writeFileSync(join(root, "assets", "secondary.html"), "<p>secondary</p>", "utf8");
    writeFileSync(join(root, "private.txt"), "private", "utf8");
    const fetchFile = async () => new Response("unexpected");

    for (const url of [
      "app://local/assets",
      "app://local/assets/secondary.html",
      "app://local/private.txt",
      "app://local/missing.js"
    ]) {
      const response = await handleRendererProtocolRequest(
        realpathSync(root),
        { method: "GET", url },
        fetchFile
      );
      expect(response.status, url).toBe(404);
    }
  });

  it("rejects methods other than GET and HEAD before file lookup", async () => {
    const root = createTemporaryDirectory("lengrvis-renderer-method-");
    const response = await handleRendererProtocolRequest(
      realpathSync(root),
      { method: "POST", url: "app://local/index.html" },
      async () => new Response("unexpected")
    );

    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe("GET, HEAD");
  });

  it("trusts only the packaged renderer entry document", () => {
    expect(isPackagedRendererEntryUrl("app://local/index.html")).toBe(true);
    expect(isPackagedRendererEntryUrl("app://local/")).toBe(true);
    expect(isPackagedRendererEntryUrl("app://local/index.html#route")).toBe(true);
    expect(isPackagedRendererEntryUrl("app://local/index.html?view=settings")).toBe(true);
    expect(isPackagedRendererEntryUrl("app://local/assets/app.js")).toBe(false);
    expect(isPackagedRendererEntryUrl("app://local/assets/../index.html")).toBe(false);
    expect(isPackagedRendererEntryUrl("app://local/%2e%2e/index.html")).toBe(false);
    expect(isPackagedRendererEntryUrl("app://lo\tcal/index.html")).toBe(false);
    expect(isPackagedRendererEntryUrl("app://evil/index.html")).toBe(false);
    expect(isPackagedRendererEntryUrl("file:///D:/app/renderer/index.html")).toBe(false);
  });
});
