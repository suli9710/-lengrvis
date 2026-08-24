import { createConnection, createServer, type Server, type Socket } from "node:net";
import {
  createServer as createHttpServer,
  request as httpRequest,
  type IncomingHttpHeaders
} from "node:http";
import { afterEach, describe, expect, it } from "vitest";

import { BrowserHostPinnedProxy } from "./browserHostPinnedProxy";

const servers: Server[] = [];
const sockets: Socket[] = [];

afterEach(async () => {
  for (const socket of sockets.splice(0)) socket.destroy();
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => server.close(() => resolve()))));
});

describe("BrowserHostPinnedProxy", () => {
  it("connects a tunnel to the resolved IP instead of the untrusted hostname", async () => {
    const upstream = createServer((socket) => socket.pipe(socket));
    servers.push(upstream);
    const upstreamPort = await listen(upstream);
    const resolved: Array<[string, number]> = [];
    const proxy = await BrowserHostPinnedProxy.start({
      resolveTarget: async (hostname, port) => {
        resolved.push([hostname, port]);
        return { address: "127.0.0.1", port };
      }
    });
    servers.push(proxy.server);

    const client = createConnection(proxy.address);
    sockets.push(client);
    await onceConnected(client);
    client.write(
      `CONNECT rebind.invalid:${upstreamPort} HTTP/1.1\r\nHost: rebind.invalid:${upstreamPort}\r\n\r\n`
    );
    const response = await onceData(client);
    expect(response.toString("ascii")).toMatch(/^HTTP\/1\.1 200/);

    client.write("pinned-connect");
    expect((await onceData(client)).toString()).toBe("pinned-connect");
    expect(resolved).toEqual([["rebind.invalid", upstreamPort]]);
  });

  it("forwards HTTP requests to the pinned IP while preserving the original Host header", async () => {
    let receivedHost: string | undefined;
    let receivedUrl: string | undefined;
    const upstream = createHttpServer((request, response) => {
      receivedHost = request.headers.host;
      receivedUrl = request.url;
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end('{"ok":true}');
    });
    servers.push(upstream);
    const upstreamPort = await listen(upstream);
    const proxy = await BrowserHostPinnedProxy.start({
      resolveTarget: async (_hostname, port) => ({ address: "127.0.0.1", port })
    });
    servers.push(proxy.server);

    const targetPath = "/hello?next=%3Cscript%3Ealert%281%29%3C%2Fscript%3E";
    const result = await requestBody(
      proxy.address,
      `http://rebind.invalid:${upstreamPort}${targetPath}`
    );

    expect(result.status).toBe(200);
    expect(result.text).toBe('{"ok":true}');
    expect(receivedHost).toBe(`rebind.invalid:${upstreamPort}`);
    expect(receivedUrl).toBe(targetPath);
  });

  it("preserves end-to-end response headers and strips hop-by-hop response headers", async () => {
    const upstream = createHttpServer((_request, response) => {
      response.setHeader("Content-Type", "text/plain; charset=utf-8");
      response.setHeader("Content-Security-Policy", "default-src 'none'");
      response.setHeader("Set-Cookie", ["session=one; HttpOnly", "theme=dark; SameSite=Lax"]);
      response.setHeader("Access-Control-Expose-Headers", "X-App-Protocol");
      response.setHeader("X-App-Protocol", "v2");
      response.setHeader("Connection", "X-Hop-Only");
      response.setHeader("X-Hop-Only", "must-not-cross");
      response.end("end-to-end headers preserved");
    });
    servers.push(upstream);
    const upstreamPort = await listen(upstream);
    const proxy = await BrowserHostPinnedProxy.start({
      resolveTarget: async (_hostname, port) => ({ address: "127.0.0.1", port })
    });
    servers.push(proxy.server);

    const result = await requestBody(proxy.address, `http://headers.invalid:${upstreamPort}/`);

    expect(result.status).toBe(200);
    expect(result.headers["content-type"]).toBe("text/plain; charset=utf-8");
    expect(result.headers["content-security-policy"]).toBe("default-src 'none'");
    expect(result.headers["set-cookie"]).toEqual([
      "session=one; HttpOnly",
      "theme=dark; SameSite=Lax"
    ]);
    expect(result.headers["access-control-expose-headers"]).toBe("X-App-Protocol");
    expect(result.headers["x-app-protocol"]).toBe("v2");
    expect(result.headers["x-hop-only"]).toBeUndefined();
  });

  it("fails closed when an upstream Connection option is not a valid header token", async () => {
    const blocked: string[] = [];
    const upstream = createHttpServer((_request, response) => {
      response.setHeader("Connection", "invalid option");
      response.end("must not reach the browser");
    });
    servers.push(upstream);
    const upstreamPort = await listen(upstream);
    const proxy = await BrowserHostPinnedProxy.start({
      resolveTarget: async (_hostname, port) => ({ address: "127.0.0.1", port }),
      onBlock: (message) => blocked.push(message)
    });
    servers.push(proxy.server);

    const result = await requestBody(proxy.address, `http://headers.invalid:${upstreamPort}/`);

    expect(result.status).toBe(502);
    expect(result.text).toBe("");
    expect(blocked).toHaveLength(1);
    expect(blocked[0]).toContain("invalid option");
  });

  it("converts resolver failures into bounded 403 responses and reports the block", async () => {
    const blocked: string[] = [];
    const proxy = await BrowserHostPinnedProxy.start({
      resolveTarget: async () => {
        throw new Error("private target blocked");
      },
      onBlock: (message) => blocked.push(message)
    });
    servers.push(proxy.server);

    const http = await requestBody(proxy.address, "http://blocked.invalid/private");
    expect(http.status).toBe(403);

    const client = createConnection(proxy.address);
    sockets.push(client);
    await onceConnected(client);
    client.write("CONNECT blocked.invalid:443 HTTP/1.1\r\nHost: blocked.invalid:443\r\n\r\n");
    const tunnelResponse = await onceData(client);

    expect(tunnelResponse.toString("ascii")).toMatch(/^HTTP\/1\.1 403/);
    expect(blocked).toEqual(["private target blocked", "private target blocked"]);
  });
});

function listen(server: Server): Promise<number> {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") return reject(new Error("missing server address"));
      resolve(address.port);
    });
  });
}

function onceConnected(socket: Socket): Promise<void> {
  return new Promise((resolve, reject) => {
    socket.once("connect", resolve);
    socket.once("error", reject);
  });
}

function onceData(socket: Socket): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    socket.once("data", resolve);
    socket.once("error", reject);
  });
}

function requestBody(
  proxy: { host: string; port: number },
  target: string
): Promise<{ status: number; text: string; headers: IncomingHttpHeaders }> {
  return new Promise((resolve, reject) => {
    const request = httpRequest(
      { host: proxy.host, port: proxy.port, method: "GET", path: target },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        response.on("end", () =>
          resolve({
            status: response.statusCode ?? 0,
            text: Buffer.concat(chunks).toString(),
            headers: response.headers
          })
        );
      }
    );
    request.once("error", reject);
    request.end();
  });
}
