import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import type { BackendApproval } from "./api";

interface NotificationSubscription {
  remove: () => void;
}

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
  await ensureNotificationChannel();
  if (!Device.isDevice) return false;
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  const next = await Notifications.requestPermissionsAsync();
  return next.granted;
}

export async function notifyApproval(approval: BackendApproval): Promise<void> {
  await ensureNotificationChannel();
  await Notifications.scheduleNotificationAsync({
    content: {
      title: "Mavris needs approval",
      body: approval.message || "A task is waiting for your decision.",
      data: { approvalId: approval.id },
      sound: "default",
      priority: Notifications.AndroidNotificationPriority.HIGH,
    },
    trigger: null,
  });
}

export function addApprovalNotificationResponseListener(
  listener: (approvalId: string) => void,
): NotificationSubscription {
  return Notifications.addNotificationResponseReceivedListener((response) => {
    const approvalId = approvalIdFromNotificationData(response.notification.request.content.data);
    if (approvalId) listener(approvalId);
  });
}

export function getLastApprovalNotificationApprovalId(): string | null {
  try {
    const response = Notifications.getLastNotificationResponse();
    const approvalId = approvalIdFromNotificationData(response?.notification.request.content.data);
    if (approvalId) Notifications.clearLastNotificationResponse();
    return approvalId;
  } catch {
    return null;
  }
}

async function ensureNotificationChannel(): Promise<void> {
  if (Platform.OS !== "android") return;
  await Notifications.setNotificationChannelAsync("approvals", {
    name: "Approvals",
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: "#0e5f76",
  });
}

function approvalIdFromNotificationData(data: Record<string, unknown> | undefined): string | null {
  const approvalId = data?.approvalId;
  return typeof approvalId === "string" && approvalId ? approvalId : null;
}
