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
  expires_at: "2026-06-01T00:05:00.000Z",
  expires_in: 300,
  server: {
    host: "desktop.example.test",
    port: 8443,
    scheme: "https",
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
assert.equal(parsedQrValue.expires_at, "2026-06-01T00:05:00.000Z");
assert.equal(parsedQrValue.server.origin, parsedQrValue.base_url);
assert.equal(parsedQrValue.server.scheme, "https");
assert.equal(parsedQrValue.server.transport_security.fingerprint_sha256, "00112233445566778899aabbccddeeff");
assert.equal(parsedQrValue.https_enabled, true);
assert.equal(parsedQrValue.trust_required, true);
assert.ok(qrContent.value.length > pairing.code.length, "QR content must include the server payload, not only the short code");

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
  assert.match(settingsSource, /QRCode\.toDataURL\(qrContent\.value/);
  assert.match(settingsSource, /<img className="mobile-pairing__qr-image" src=\{qrImage\}/);
  assert.match(settingsSource, /手机端会阻断 token 配对/);
  assert.doesNotMatch(settingsSource, /data-qr-text=\{qrContent\.value\}/);
  assert.doesNotMatch(settingsSource, /mobile-pairing__payload-text/);
  assert.doesNotMatch(settingsSource, /mobile-pairing__qr-text/);
  assert.doesNotMatch(settingsSource, /<span>QR 内容<\/span>/);
  console.log("desktop mobile pairing QR smoke passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
