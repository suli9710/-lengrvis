import * as Device from "expo-device";
import * as Notifications from "expo-notifications";

import type { BackendApproval } from "./api";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowAlert: true,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

export async function requestNotificationPermission(): Promise<boolean> {
  if (!Device.isDevice) return false;
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  const next = await Notifications.requestPermissionsAsync();
  return next.granted;
}

export async function notifyApproval(approval: BackendApproval): Promise<void> {
  await Notifications.scheduleNotificationAsync({
    content: {
      title: "Mavris needs approval",
      body: approval.message || "A task is waiting for your decision.",
      data: { approvalId: approval.id },
    },
    trigger: null,
  });
}
