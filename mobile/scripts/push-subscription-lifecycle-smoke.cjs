const assert = require("node:assert/strict");
const fs = require("node:fs");

const { loadTsModule, mobilePath } = require("./behavior-smoke-helpers.cjs");

async function main() {
  const lifecycle = loadTsModule(mobilePath("src/pushSubscriptionLifecycle.ts"));
  const session = {
    baseUrl: "https://desktop.example.test",
    token: "paired-session-token",
    deviceId: "phone-1",
    baseUrlSecurity: { normalizedBaseUrl: "https://desktop.example.test" },
  };
  const subscription = { provider: "expo", token: "ExponentPushToken[lifecycle-smoke]" };
  const registrations = [];
  const unregistrations = [];

  const registered = await lifecycle.ensureApprovalPushSubscription(session, {
    requestSubscription: async () => subscription,
    registerSubscription: async (nextSession, nextSubscription) => {
      registrations.push([nextSession, nextSubscription]);
    },
    unregisterSubscription: async (nextSession) => {
      unregistrations.push(nextSession);
    },
  });
  assert.equal(registered.status, "registered");
  assert.ok(registered.registrationKey);
  assert.deepEqual(registrations, [[session, subscription]]);

  const alreadyRegistered = await lifecycle.ensureApprovalPushSubscription(session, {
    requestSubscription: async () => subscription,
    registerSubscription: async () => {
      throw new Error("an unchanged Expo token must not be registered twice");
    },
    unregisterSubscription: async () => {
      throw new Error("an available unchanged token must not unregister");
    },
  }, registered.registrationKey);
  assert.equal(alreadyRegistered.status, "already_registered");

  const unavailable = await lifecycle.ensureApprovalPushSubscription(session, {
    requestSubscription: async () => null,
    registerSubscription: async () => {
      throw new Error("a missing device token must never be registered");
    },
    unregisterSubscription: async (nextSession) => {
      unregistrations.push(nextSession);
    },
  });
  assert.equal(unavailable.status, "unavailable");
  assert.deepEqual(unregistrations, [session], "revoked notification permission must remove any persisted subscription");

  assert.equal(lifecycle.approvalPushRegistrationRetryDelayMs(0), 60_000);
  assert.equal(lifecycle.approvalPushRegistrationRetryDelayMs(3), 8 * 60_000);
  assert.equal(lifecycle.approvalPushRegistrationRetryDelayMs(20), 15 * 60_000);

  const contextSource = fs.readFileSync(mobilePath("src/state/MobileCompanionContext.tsx"), "utf8");
  assert.match(
    contextSource,
    /ensureApprovalPushSubscription\(\s*session,\s*\{[\s\S]*requestSubscription: requestApprovalPushSubscription,[\s\S]*registerSubscription: registerMobilePushSubscription,/,
    "a paired app must register its permission-approved device token with the paired desktop",
  );
  assert.doesNotMatch(
    contextSource,
    /void notifyApproval\(payload\.approval\)/,
    "a live foreground stream must not duplicate the background push notification",
  );
  assert.match(
    contextSource,
    /approvalPushRegistrationRetryDelayMs\(pushRegistrationAttempt\)/,
    "transient provider or network failures must retry push registration",
  );
  assert.match(
    contextSource,
    /if \(state !== "active"\) return;\s*setPushRegistrationAttempt/,
    "returning from system notification settings must recheck registration and revocation",
  );
}

main()
  .then(() => console.log("Mobile push subscription lifecycle smoke passed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
