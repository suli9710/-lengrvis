const assert = require("node:assert/strict");
const fs = require("node:fs");

const { loadTsModule, mobilePath } = require("./behavior-smoke-helpers.cjs");

function assertSourceIncludes(source, expected, message) {
  assert.ok(source.includes(expected), `${message}: expected source to include ${JSON.stringify(expected)}`);
}

async function assertNativeTlsTrustRuntimeBoundaries(client) {
  const source = fs.readFileSync(mobilePath("src/api/client/nativeTlsTrust.ts"), "utf8");
  assertSourceIncludes(
    source,
    "iOS LAN certificate pinning is not available yet",
    "iOS must fail closed when local LAN certificate pinning would be required",
  );
  assertSourceIncludes(
    source,
    "This mobile runtime cannot configure LAN certificate pinning for local HTTPS pairing.",
    "Non-Android runtimes must fail closed when local LAN certificate pinning would be required",
  );
  assert.doesNotMatch(
    source,
    /attestation_verified:\s*true|hardware_attestation|hardware attested/i,
    "native TLS trust source must not claim hardware device attestation",
  );

  const pinnedSecurity = client.describeBaseUrlSecurity("https://example.test:8443", {
    transport: { http_scheme: "https", websocket_scheme: "wss", tls_enabled: true },
    tls: {
      enabled: true,
      trust_status: "requires_trust",
      requires_trust: true,
      self_signed: true,
      fingerprint_sha256: "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99",
    },
  });
  const calls = [];
  const pinRecord = (fingerprint, status = "active", expiresAt = Date.now() + 60_000, origin = "https://example.test:8443") => ({
    schema_version: "tls-pin-record-v1",
    pin_id: `pin-${status}`,
    origin,
    host: new URL(origin).hostname.replace(/^\[|\]$/g, "").toLowerCase(),
    fingerprint_sha256: fingerprint,
    status,
    created_at: new Date(Date.now() - 1000).toISOString(),
    expires_at: new Date(expiresAt).toISOString(),
    ...(status === "revoked" ? { revoked_at: new Date().toISOString() } : {}),
  });
  const androidTrust = loadTsModule(mobilePath("src/api/client/nativeTlsTrust.ts"), {
    require: (id) => {
      if (id === "react-native") {
        return {
          Platform: { OS: "android" },
          NativeModules: {
            LengrvisLanTrust: {
              stageServerCertificate: async (baseUrl, fingerprint, activeExpiry, nextExpiry, sourceDeviceId) => {
                calls.push({ stage: true, baseUrl, fingerprint, activeExpiry, nextExpiry, sourceDeviceId });
                return { ...pinRecord(fingerprint, "active", activeExpiry, baseUrl), source_device_id: sourceDeviceId };
              },
              assertServerCertificateTrusted: async (baseUrl, fingerprint) => {
                calls.push({ assert: true, baseUrl, fingerprint });
                return pinRecord(fingerprint, "active", Date.now() + 60_000, baseUrl);
              },
              activateServerCertificate: async (baseUrl, fingerprint, activeExpiry, sourceDeviceId) => {
                calls.push({ activate: true, baseUrl, fingerprint, activeExpiry, sourceDeviceId });
                return { ...pinRecord(fingerprint, "active", activeExpiry, baseUrl), source_device_id: sourceDeviceId };
              },
              listServerCertificatePins: async (baseUrl, includeRevoked) => {
                calls.push({ list: true, baseUrl, includeRevoked });
                return [pinRecord("aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899", "active", Date.now() + 60_000, baseUrl)];
              },
              revokeServerCertificate: async (baseUrl, fingerprint) => {
                calls.push({ revoke: true, baseUrl, fingerprint });
                return true;
              },
              clearTrustedServers: async () => calls.push({ clear: true }),
            },
          },
        };
      }
      return require(id);
    },
  });
  const expectedFingerprint = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899";
  const staged = await androidTrust.stageNativeTlsTrust(pinnedSecurity, "desktop-device-1");
  assert.equal(staged.schema_version, "tls-pin-record-v1");
  assert.equal(staged.fingerprint_sha256, expectedFingerprint);
  assert.equal(calls[0].stage, true);
  assert.equal(calls[0].baseUrl, "https://example.test:8443");
  assert.equal(calls[0].fingerprint, expectedFingerprint);
  assert.equal(calls[0].sourceDeviceId, "desktop-device-1");
  assert.ok(calls[0].activeExpiry > calls[0].nextExpiry, "rotation overlap must expire sooner than an active pin");
  await androidTrust.configureNativeTlsTrust(pinnedSecurity);
  assert.deepEqual(calls[1], { assert: true, baseUrl: "https://example.test:8443", fingerprint: expectedFingerprint });
  await androidTrust.activateNativeTlsTrust(pinnedSecurity, "desktop-device-1");
  assert.equal(calls[2].activate, true);
  assert.equal(calls[2].sourceDeviceId, "desktop-device-1");
  assert.equal((await androidTrust.listNativeTlsPins("https://example.test:8443")).length, 1);
  await androidTrust.revokeNativeTlsPin("https://example.test:8443", expectedFingerprint);
  await androidTrust.clearNativeTlsTrust();
  assert.deepEqual(calls.at(-1), { clear: true });

  const ipv6Security = client.describeBaseUrlSecurity("https://[2001:db8::1]:8443", {
    transport: { http_scheme: "https", websocket_scheme: "wss", tls_enabled: true },
    tls: {
      enabled: true,
      trust_status: "requires_trust",
      requires_trust: true,
      self_signed: true,
      fingerprint_sha256: expectedFingerprint,
    },
  });
  const ipv6Pin = await androidTrust.stageNativeTlsTrust(ipv6Security);
  assert.equal(ipv6Pin.origin, "https://[2001:db8::1]:8443");
  assert.equal(ipv6Pin.host, "2001:db8::1");

  const idnaSecurity = client.describeBaseUrlSecurity("https://例子.测试:8443", {
    transport: { http_scheme: "https", websocket_scheme: "wss", tls_enabled: true },
    tls: {
      enabled: true,
      trust_status: "requires_trust",
      requires_trust: true,
      self_signed: true,
      fingerprint_sha256: expectedFingerprint,
    },
  });
  const idnaPin = await androidTrust.stageNativeTlsTrust(idnaSecurity);
  assert.equal(idnaPin.origin, "https://xn--fsqu00a.xn--0zwm56d:8443");
  assert.equal(idnaPin.host, "xn--fsqu00a.xn--0zwm56d");

  const expiredTrust = loadTsModule(mobilePath("src/api/client/nativeTlsTrust.ts"), {
    require: (id) => {
      if (id === "react-native") {
        return {
          Platform: { OS: "android" },
          NativeModules: {
            LengrvisLanTrust: {
              assertServerCertificateTrusted: async (_baseUrl, fingerprint) => pinRecord(fingerprint, "active", Date.now() - 1),
            },
          },
        };
      }
      return require(id);
    },
  });
  await assert.rejects(
    () => expiredTrust.configureNativeTlsTrust(pinnedSecurity),
    (error) => error?.name === "TlsTrustConfigurationError" && /expired|revoked/.test(String(error.message)),
    "expired native TLS pins must fail closed instead of being silently renewed",
  );

  for (const osName of ["ios", "web"]) {
    const trust = loadTsModule(mobilePath("src/api/client/nativeTlsTrust.ts"), {
      require: (id) => {
        if (id === "react-native") return { Platform: { OS: osName }, NativeModules: {} };
        return require(id);
      },
    });
    await assert.rejects(
      () => trust.stageNativeTlsTrust(pinnedSecurity),
      (error) => error?.name === "TlsTrustConfigurationError" && /pinning|runtime/.test(String(error.message)),
    );
  }
}

module.exports = { assertNativeTlsTrustRuntimeBoundaries };
