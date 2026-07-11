import { createConnection, createServer, type Server, type Socket } from "node:net";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
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
    const upstream = createHttpServer((request, response) => {
      const body = `${request.headers.host} ${request.url}`;
      response.writeHead(200, { "Content-Length": Buffer.byteLength(body) });
      response.end(body);
    });
    servers.push(upstream);
    const upstreamPort = await listen(upstream);
    const proxy = await BrowserHostPinnedProxy.start({
      resolveTarget: async (_hostname, port) => ({ address: "127.0.0.1", port })
    });
    servers.push(proxy.server);

    const body = await requestBody(proxy.address, `http://rebind.invalid:${upstreamPort}/hello?x=1`);

    expect(body.status).toBe(200);
    expect(body.text).toBe(`rebind.invalid:${upstreamPort} /hello?x=1`);
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
): Promise<{ status: number; text: string }> {
  return new Promise((resolve, reject) => {
    const request = httpRequest(
      { host: proxy.host, port: proxy.port, method: "GET", path: target },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        response.on("end", () =>
          resolve({ status: response.statusCode ?? 0, text: Buffer.concat(chunks).toString() })
        );
      }
    );
    request.once("error", reject);
    request.end();
  });
}
