import {
  createServer,
  request as httpRequest,
  type IncomingHttpHeaders,
  type IncomingMessage,
  type Server,
  type ServerResponse
} from "node:http";
import { connect, type Socket } from "node:net";
import type { Duplex } from "node:stream";

import { resolveBrowserHostPinnedAddress } from "./browserHostNetworkGuard";

export interface BrowserHostPinnedTarget {
  address: string;
  port: number;
}

export type BrowserHostPinnedTargetResolver = (
  hostname: string,
  port: number
) => Promise<BrowserHostPinnedTarget>;

interface BrowserHostPinnedProxyOptions {
  resolveTarget?: BrowserHostPinnedTargetResolver;
  onBlock?: (error: string) => void;
}

export class BrowserHostPinnedProxy {
  readonly server: Server;
  private readonly resolveTarget: BrowserHostPinnedTargetResolver;
  private readonly onBlock?: (error: string) => void;

  private constructor(options: BrowserHostPinnedProxyOptions) {
    this.resolveTarget = options.resolveTarget ?? defaultResolveTarget;
    this.onBlock = options.onBlock;
    this.server = createServer((request, response) => {
      void this.handleHttpRequest(request, response);
    });
    this.server.on("connect", (request, client, head) => {
      void this.handleConnect(request, client, head);
    });
  }

  static async start(options: BrowserHostPinnedProxyOptions = {}): Promise<BrowserHostPinnedProxy> {
    const proxy = new BrowserHostPinnedProxy(options);
    await new Promise<void>((resolve, reject) => {
      proxy.server.once("error", reject);
      proxy.server.listen(0, "127.0.0.1", () => resolve());
    });
    return proxy;
  }

  get address(): { host: string; port: number } {
    const address = this.server.address();
    if (!address || typeof address === "string") {
      throw new Error("BrowserHost pinned proxy is not listening");
    }
    return { host: "127.0.0.1", port: address.port };
  }

  get url(): string {
    const { host, port } = this.address;
    return `http://${host}:${port}`;
  }

  async close(): Promise<void> {
    if (!this.server.listening) return;
    await new Promise<void>((resolve) => this.server.close(() => resolve()));
  }

  private async handleConnect(request: IncomingMessage, client: Duplex, head: Buffer): Promise<void> {
    let upstream: Socket | undefined;
    try {
      const { hostname, port } = parseAuthority(request.url ?? "", 443);
      const target = await this.resolveTarget(hostname, port);
      upstream = connect({ host: target.address, port: target.port });
      upstream.setTimeout(30_000, () => upstream?.destroy(new Error("Pinned proxy tunnel timed out")));
      await onceConnected(upstream);
      client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      if (head.length > 0) upstream.write(head);
      upstream.pipe(client);
      client.pipe(upstream);
      const closeBoth = (): void => {
        client.destroy();
        upstream?.destroy();
      };
      client.once("error", closeBoth);
      upstream.once("error", closeBoth);
    } catch (error) { // broad-exception-boundary: convert all resolver/socket failures into a closed tunnel and explicit 403.
      const message = safeErrorMessage(error);
      this.onBlock?.(message);
      if (!client.destroyed) {
        client.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
      }
      upstream?.destroy();
    }
  }

  private async handleHttpRequest(request: IncomingMessage, response: ServerResponse): Promise<void> {
    try {
      const parsed = new URL(request.url ?? "");
      if (parsed.protocol !== "http:" || !parsed.hostname) {
        throw new Error("Only absolute HTTP proxy targets are allowed");
      }
      const port = parsed.port ? Number.parseInt(parsed.port, 10) : 80;
      const target = await this.resolveTarget(parsed.hostname, port);
      const upstream = httpRequest({
        host: target.address,
        port: target.port,
        method: request.method,
        path: `${parsed.pathname || "/"}${parsed.search}`,
        headers: forwardedHeaders(request.headers, parsed.host)
      });
      upstream.once("error", (error) => {
        this.onBlock?.(safeErrorMessage(error));
        if (!response.headersSent) response.writeHead(502, { Connection: "close" });
        response.end();
      });
      upstream.once("response", (upstreamResponse) => {
        response.writeHead(
          upstreamResponse.statusCode ?? 502,
          upstreamResponse.statusMessage,
          upstreamResponse.headers
        );
        upstreamResponse.pipe(response);
      });
      request.pipe(upstream);
    } catch (error) { // broad-exception-boundary: proxy boundary must fail closed before forwarding an unpinned request.
      const message = safeErrorMessage(error);
      this.onBlock?.(message);
      response.writeHead(403, { Connection: "close", "Content-Length": "0" });
      response.end();
    }
  }
}

async function defaultResolveTarget(hostname: string, port: number): Promise<BrowserHostPinnedTarget> {
  return { address: await resolveBrowserHostPinnedAddress(hostname), port };
}

function parseAuthority(authority: string, defaultPort: number): { hostname: string; port: number } {
  const parsed = new URL(`http://${String(authority || "").trim()}`);
  const port = parsed.port ? Number.parseInt(parsed.port, 10) : defaultPort;
  if (!parsed.hostname || !Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error("Invalid BrowserHost proxy target");
  }
  return { hostname: parsed.hostname.replace(/^\[|\]$/g, ""), port };
}

function onceConnected(socket: Socket): Promise<void> {
  return new Promise((resolve, reject) => {
    socket.once("connect", resolve);
    socket.once("error", reject);
  });
}

function safeErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "Blocked BrowserHost proxy target");
}

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "upgrade"
]);

function forwardedHeaders(headers: IncomingHttpHeaders, authority: string): IncomingHttpHeaders {
  const forwarded: IncomingHttpHeaders = {};
  for (const [name, value] of Object.entries(headers)) {
    if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase()) && name.toLowerCase() !== "host") {
      forwarded[name] = value;
    }
  }
  forwarded.host = authority;
  forwarded.connection = "close";
  return forwarded;
}
