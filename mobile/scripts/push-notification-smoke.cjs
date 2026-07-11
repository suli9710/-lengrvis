const assert = require("node:assert/strict");

const { loadTsModule, mobilePath } = require("./behavior-smoke-helpers.cjs");

async function main() {
  let permissionGranted = true;
  let tokenFailure = null;
  const calls = [];
  const notifications = {
    AndroidImportance: { HIGH: 4 },
    AndroidNotificationPriority: { HIGH: "high" },
    setNotificationHandler: () => undefined,
    setNotificationChannelAsync: async (...args) => calls.push(["channel", ...args]),
    getPermissionsAsync: async () => ({ granted: permissionGranted }),
    requestPermissionsAsync: async () => ({ granted: permissionGranted }),
    getExpoPushTokenAsync: async () => {
      if (tokenFailure) throw tokenFailure;
      return { type: "expo", data: "ExponentPushToken[push-smoke]" };
    },
    scheduleNotificationAsync: async () => "notification-id",
    addNotificationResponseReceivedListener: () => ({ remove: () => undefined }),
    getLastNotificationResponse: () => null,
    clearLastNotificationResponse: () => undefined,
  };
  const module = loadTsModule(mobilePath("src/notifications.ts"), {
    require: (id) => {
      if (id === "expo-device") return { isDevice: true };
      if (id === "expo-notifications") return notifications;
      if (id === "react-native") {
        return {
          NativeModules: { ExponentConstants: { appOwnership: "standalone" } },
          Platform: { OS: "android" },
        };
      }
      return require(id);
    },
  });

  assert.deepEqual(
    JSON.parse(JSON.stringify(await module.requestApprovalPushSubscription())),
    { provider: "expo", token: "ExponentPushToken[push-smoke]" },
  );
  assert.equal(calls.some(([name]) => name === "channel"), true);

  permissionGranted = false;
  assert.equal(await module.requestApprovalPushSubscription(), null);

  permissionGranted = true;
  tokenFailure = new Error("provider failed for ExponentPushToken[do-not-leak]");
  await assert.rejects(
    () => module.requestApprovalPushSubscription(),
    (error) => {
      assert.equal(error.name, "PushNotificationRegistrationError");
      assert.doesNotMatch(error.message, /ExponentPushToken|do-not-leak|token/i);
      return true;
    },
  );
}

main()
  .then(() => console.log("Mobile push notification smoke passed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
