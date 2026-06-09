const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const MOBILE_ROOT = path.resolve(__dirname, "..");

function mobilePath(...segments) {
  return path.resolve(MOBILE_ROOT, ...segments);
}

function loadTsModule(modulePath, sandboxOverrides = {}) {
  const source = fs.readFileSync(modulePath, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      strict: true,
    },
  }).outputText;

  const sandbox = {
    exports: {},
    module: { exports: {} },
    require,
    URL,
    fetch: globalThis.fetch,
    console,
    setTimeout,
    clearTimeout,
    ...sandboxOverrides,
  };
  sandbox.exports = sandbox.module.exports;
  vm.runInNewContext(compiled, sandbox, { filename: modulePath });
  return sandbox.module.exports;
}

function loadMobileClient(sandboxOverrides = {}) {
  return loadTsModule(mobilePath("src/api/client.ts"), sandboxOverrides);
}

async function startHttpWsSmokeServer({ handleRequest, handleUpgrade }) {
  const requests = [];
  const upgrades = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "127.0.0.1"}`);
    const bodyText = await readRequestBody(req);
    const request = {
      method: req.method ?? "GET",
      url: req.url ?? "/",
      path: url.pathname,
      headers: req.headers,
      bodyText,
      json: parseJsonBody(bodyText),
    };
    requests.push(request);

    try {
      const handled = await handleRequest({ req, res, url, request, requests });
      if (handled === false && !res.writableEnded) {
        jsonResponse(res, 404, { detail: `Unhandled smoke route: ${request.method} ${request.path}` });
      }
    } catch (error) {
      if (!res.headersSent) {
        jsonResponse(res, 500, { detail: error?.message ?? String(error) });
      } else {
        res.destroy(error);
      }
    }
  });

  server.on("upgrade", (req, socket, head) => {
    const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "127.0.0.1"}`);
    const upgrade = {
      path: url.pathname,
      headers: req.headers,
      protocols: parseWebSocketProtocols(req.headers["sec-websocket-protocol"]),
      accepted: false,
    };
    upgrades.push(upgrade);

    Promise.resolve(handleUpgrade({ req, socket, head, url, upgrade, upgrades })).catch((error) => {
      rejectWebSocketUpgrade(socket, 500, error?.message ?? String(error));
    });
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });

  const address = server.address();
  assert.equal(typeof address, "object");
  assert.ok(address);
  const origin = `http://127.0.0.1:${address.port}`;

  return {
    origin,
    requests,
    upgrades,
    close: () =>
      new Promise((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}

function jsonResponse(res, status, body, headers = {}) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
    ...headers,
  });
  res.end(payload);
}

function assertJsonRequest(request, { method, path, authorization, body } = {}) {
  if (method) assert.equal(request.method, method);
  if (path) assert.equal(request.path, path);
  if (authorization) assert.equal(request.headers.authorization, authorization);
  if (body !== undefined) assert.deepEqual(request.json, body);
}

function acceptWebSocketUpgrade(req, socket, protocol) {
  const key = req.headers["sec-websocket-key"];
  assert.equal(typeof key, "string");
  const accept = crypto.createHash("sha1").update(`${key}${WEBSOCKET_GUID}`).digest("base64");
  const headers = [
    "HTTP/1.1 101 Switching Protocols",
    "Upgrade: websocket",
    "Connection: Upgrade",
    `Sec-WebSocket-Accept: ${accept}`,
  ];
  if (protocol) headers.push(`Sec-WebSocket-Protocol: ${protocol}`);
  socket.write(`${headers.join("\r\n")}\r\n\r\n`);
  socket.end();
}

function rejectWebSocketUpgrade(socket, status = 401, detail = "Unauthorized") {
  const reason = status === 403 ? "Forbidden" : status === 410 ? "Gone" : status === 404 ? "Not Found" : "Unauthorized";
  const payload = JSON.stringify({ detail });
  socket.write(
    [
      `HTTP/1.1 ${status} ${reason}`,
      "Connection: close",
      "Content-Type: application/json",
      `Content-Length: ${Buffer.byteLength(payload)}`,
      "",
      payload,
    ].join("\r\n"),
  );
  socket.end();
}

function connectWebSocket(url, protocols = [], timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    assert.equal(parsed.protocol, "ws:", "local behavior smoke only opens ws:// endpoints");
    const key = crypto.randomBytes(16).toString("base64");
    const port = Number(parsed.port || 80);
    const socket = net.createConnection({ host: parsed.hostname, port });
    let buffer = "";
    let settled = false;
    const timer = setTimeout(() => fail(new Error(`Timed out opening WebSocket smoke connection to ${url}`)), timeoutMs);

    function cleanup() {
      clearTimeout(timer);
      socket.removeAllListeners();
      if (!socket.destroyed) socket.destroy();
    }

    function fail(error) {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    }

    function done(result) {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(result);
    }

    socket.on("connect", () => {
      const pathname = `${parsed.pathname}${parsed.search}`;
      const headers = [
        `GET ${pathname} HTTP/1.1`,
        `Host: ${parsed.host}`,
        "Upgrade: websocket",
        "Connection: Upgrade",
        `Sec-WebSocket-Key: ${key}`,
        "Sec-WebSocket-Version: 13",
      ];
      if (protocols.length > 0) headers.push(`Sec-WebSocket-Protocol: ${protocols.join(", ")}`);
      socket.write(`${headers.join("\r\n")}\r\n\r\n`);
    });
    socket.on("data", (chunk) => {
      buffer += chunk.toString("latin1");
      if (!buffer.includes("\r\n\r\n")) return;
      try {
        const result = parseHttpResponseHead(buffer, key);
        done(result);
      } catch (error) {
        fail(error);
      }
    });
    socket.on("error", fail);
    socket.on("end", () => {
      if (!settled && buffer) {
        try {
          done(parseHttpResponseHead(buffer, key));
        } catch (error) {
          fail(error);
        }
      }
    });
  });
}

function assertAcceptedWebSocket(handshake, expectedProtocol) {
  assert.equal(handshake.statusCode, 101);
  assert.equal(handshake.headers.upgrade?.toLowerCase(), "websocket");
  assert.equal(handshake.headers.connection?.toLowerCase(), "upgrade");
  assert.equal(handshake.headers["sec-websocket-protocol"], expectedProtocol);
  assert.equal(handshake.headers["sec-websocket-accept"], handshake.expectedAccept);
}

function parseHttpResponseHead(raw, key) {
  const [head] = raw.split("\r\n\r\n");
  const lines = head.split("\r\n");
  const statusMatch = lines[0]?.match(/^HTTP\/1\.1\s+(\d{3})/);
  assert.ok(statusMatch, `Invalid WebSocket handshake response: ${lines[0] ?? "<empty>"}`);
  const headers = {};
  for (const line of lines.slice(1)) {
    const separator = line.indexOf(":");
    if (separator < 0) continue;
    headers[line.slice(0, separator).trim().toLowerCase()] = line.slice(separator + 1).trim();
  }
  return {
    statusCode: Number(statusMatch[1]),
    headers,
    expectedAccept: crypto.createHash("sha1").update(`${key}${WEBSOCKET_GUID}`).digest("base64"),
    raw,
  };
}

function parseWebSocketProtocols(value) {
  const raw = Array.isArray(value) ? value.join(",") : value ?? "";
  return String(raw)
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function assertWebSocketTokenTransport(connectionInfo, token, { pathname, protocolPrefix = "lengrvis.mobile.token.", label = "WebSocket" } = {}) {
  const parsed = new URL(connectionInfo.url);
  if (pathname) assert.equal(parsed.pathname, pathname, `${label} URL pathname`);
  assert.equal(parsed.search, "", `${label} URL must not carry auth data in the query string`);
  assert.equal(connectionInfo.url.includes(token), false, `${label} URL must not contain the auth token`);
  assert.doesNotMatch(connectionInfo.url, /[?&](?:token|access_token|auth|authorization)=/i, `${label} URL must not use query auth`);
  assert.deepEqual(connectionInfo.protocols, [`${protocolPrefix}${token}`], `${label} token must be carried in Sec-WebSocket-Protocol`);
}

function assertInsecureLanError(error) {
  assert.equal(error?.name, "InsecureLanBaseUrlError");
  assert.equal(error?.security?.kind, "insecureLan");
  return true;
}

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("error", reject);
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });
}

function parseJsonBody(bodyText) {
  if (!bodyText) return undefined;
  try {
    return JSON.parse(bodyText);
  } catch {
    return undefined;
  }
}

module.exports = {
  acceptWebSocketUpgrade,
  assertAcceptedWebSocket,
  assertInsecureLanError,
  assertJsonRequest,
  assertWebSocketTokenTransport,
  connectWebSocket,
  jsonResponse,
  loadMobileClient,
  loadTsModule,
  mobilePath,
  rejectWebSocketUpgrade,
  startHttpWsSmokeServer,
};
