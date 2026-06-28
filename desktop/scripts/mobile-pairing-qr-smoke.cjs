const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const QRCode = require("qrcode");

const sourcePath = path.join(__dirname, "..", "src", "shared", "mobilePairingPayload.ts");
const source = fs.readFileSync(sourcePath, "utf8");
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
};
sandbox.exports = sandbox.module.exports;
vm.runInNewContext(compiled, sandbox, { filename: sourcePath });

const {
  buildMobilePairingPayload,
  buildMobilePairingQrContent,
  serializeMobilePairingPayload,
  serializeMobilePairingQrContent,
} = sandbox.module.exports;

const pairing = {
  code: "A1B2C3",
  claim_secret: "pair-claim-secret-visible-only-in-qr-123456",
  expires_at: "2026-06-01T00:05:00.000Z",
  expires_in: 300,
  token: "secret-pairing-token-must-not-render",
  token_type: "Bearer",
  server: {
    host: "desktop.example.test",
    port: 8443,
    scheme: "https",
    token: "secret-server-token-must-not-render",
    transport_security: {
      http_scheme: "https",
      websocket_scheme: "wss",
      tls_enabled: true,
      fingerprint_sha256: "00112233445566778899aabbccddeeff",
    },
  },
  https_enabled: true,
  trust_required: true,
};

const payload = buildMobilePairingPayload(pairing);
const serializedPayload = serializeMobilePairingPayload(pairing);
const qrContent = buildMobilePairingQrContent(pairing);

assert.equal(qrContent.type, "lengrvis.mobile_pairing.qr");
assert.equal(qrContent.version, 1);
assert.equal(qrContent.mime_type, "application/json");
assert.equal(qrContent.encoding, "utf-8");
assert.equal(qrContent.value, serializedPayload);
assert.equal(qrContent.value, serializeMobilePairingQrContent(pairing));
assert.equal(qrContent.length, qrContent.value.length);
assert.deepEqual(qrContent.payload, payload);

const parsedQrValue = JSON.parse(qrContent.value);
assert.equal(parsedQrValue.type, "lengrvis.mobile_pairing");
assert.equal(parsedQrValue.version, 1);
assert.equal(parsedQrValue.base_url, "https://desktop.example.test:8443");
assert.equal(parsedQrValue.code, "A1B2C3");
assert.equal(parsedQrValue.claim_secret, "pair-claim-secret-visible-only-in-qr-123456");
assert.equal(parsedQrValue.expires_at, "2026-06-01T00:05:00.000Z");
assert.equal(parsedQrValue.server.origin, parsedQrValue.base_url);
assert.equal(parsedQrValue.server.scheme, "https");
assert.equal(parsedQrValue.server.transport_security.http_scheme, "https");
assert.equal(parsedQrValue.server.transport_security.websocket_scheme, "wss");
assert.equal(parsedQrValue.server.transport_security.tls_enabled, true);
assert.equal(parsedQrValue.server.transport_security.fingerprint_sha256, "00112233445566778899aabbccddeeff");
assert.equal(parsedQrValue.transport_security.http_scheme, "https");
assert.equal(parsedQrValue.transport_security.websocket_scheme, "wss");
assert.equal(parsedQrValue.transport_security.tls_enabled, true);
assert.equal(parsedQrValue.transport_security.fingerprint_sha256, "00112233445566778899aabbccddeeff");
assert.equal(parsedQrValue.https_enabled, true);
assert.equal(parsedQrValue.trust_required, true);
assert.ok(qrContent.value.length > pairing.code.length, "QR content must include the server payload, not only the short code");
assert.equal("token" in parsedQrValue, false);
assert.equal("token_type" in parsedQrValue, false);
assert.equal("token" in parsedQrValue.server, false);
assert.doesNotMatch(qrContent.value, /secret-pairing-token-must-not-render|secret-server-token-must-not-render|Bearer/);

const lanHttpPairing = {
  ...pairing,
  server: {
    host: "192.168.1.20",
    port: 8000,
    scheme: "http",
    transport_security: {
      http_scheme: "http",
      websocket_scheme: "ws",
      tls_enabled: false,
    },
  },
  https_enabled: false,
  trust_required: false,
};
const lanHttpQrValue = JSON.parse(buildMobilePairingQrContent(lanHttpPairing).value);
assert.equal(lanHttpQrValue.base_url, "http://192.168.1.20:8000");
assert.equal(lanHttpQrValue.server.scheme, "http");
assert.equal(lanHttpQrValue.server.transport_security.http_scheme, "http");
assert.equal(lanHttpQrValue.server.transport_security.websocket_scheme, "ws");
assert.equal(lanHttpQrValue.server.transport_security.tls_enabled, false);
assert.equal(lanHttpQrValue.transport_security.http_scheme, "http");
assert.equal(lanHttpQrValue.transport_security.websocket_scheme, "ws");
assert.equal(lanHttpQrValue.transport_security.tls_enabled, false);
assert.equal(lanHttpQrValue.https_enabled, false);
assert.equal(lanHttpQrValue.trust_required, false);

async function main() {
  const dataUrl = await QRCode.toDataURL(qrContent.value, {
    errorCorrectionLevel: "M",
    margin: 2,
    width: 148,
  });
  assert.match(dataUrl, /^data:image\/png;base64,/);
  assert.ok(dataUrl.length > 500, "generated QR image should be non-empty");

  const settingsSource = fs.readFileSync(
    path.join(__dirname, "..", "src", "renderer", "components", "SettingsPanel.tsx"),
    "utf8",
  );
  const visualCodeSource = fs.readFileSync(
    path.join(__dirname, "..", "src", "renderer", "components", "settings", "PairingVisualCode.tsx"),
    "utf8",
  );
  assert.match(settingsSource, /lazy\(\(\) =>\s*import\("\.\/settings\/PairingVisualCode"\)/);
  assert.match(settingsSource, /<Suspense[\s\S]*<PairingVisualCode/);
  assert.match(visualCodeSource, /QRCode\.toDataURL\(qrContent\.value/);
  assert.match(visualCodeSource, /<img className="mobile-pairing__qr-image" src=\{qrImage\}/);
  assert.match(settingsSource, /手机扫码配对/);
  assert.match(settingsSource, /打开手机 App 的扫码入口扫二维码/);
  assert.match(settingsSource, /打开手机 App 扫码/);
  assert.match(settingsSource, /优先扫码；复制只是备用，不会在界面展开 token/);
  assert.match(settingsSource, /无需手动输入局域网地址或 token/);
  assert.match(settingsSource, /HTTPS\/WSS 会直接用于手机连接/);
  assert.match(settingsSource, /局域网 HTTP 会被拦截/);
  assert.match(settingsSource, /手机端会阻断 token 配对/);
  assert.doesNotMatch(settingsSource, /点击生成后复制整段配对信息/);
  assert.doesNotMatch(settingsSource, /手动复制下方文本/);
  assert.doesNotMatch(settingsSource, /真实手机已验证|真机已验证|已在手机验证/);
  [
    /data-qr-text=\{qrContent\.value\}/,
    /mobile-pairing__payload-text/,
    /mobile-pairing__qr-text/,
    /<span>QR 内容<\/span>/,
    /<textarea[^>]*>\s*\{qrContent\.value\}/,
    /<pre[^>]*>\s*\{qrContent\.value\}/,
    /<code[^>]*>\s*\{qrContent\.value\}/,
  ].forEach((pattern) => {
    assert.doesNotMatch(settingsSource, pattern);
    assert.doesNotMatch(visualCodeSource, pattern);
  });
  console.log("desktop mobile pairing QR smoke passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
