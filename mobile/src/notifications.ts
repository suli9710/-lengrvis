import * as Device from "expo-device";
import { NativeModules, Platform } from "react-native";

import type { BackendApproval } from "./api";

interface NotificationSubscription {
  remove: () => void;
}

type ExpoNotifications = typeof import("expo-notifications");

let notificationsModulePromise: Promise<ExpoNotifications | null> | null = null;

function isExpoGoRuntime(): boolean {
  const exponentConstants = NativeModules.ExponentConstants as
    | { appOwnership?: string; executionEnvironment?: string }
    | undefined;
  return (
    exponentConstants?.appOwnership === "expo" ||
    exponentConstants?.executionEnvironment === "storeClient"
  );
}

async function loadNotifications(): Promise<ExpoNotifications | null> {
  if (isExpoGoRuntime()) return null;
  notificationsModulePromise ??= import("expo-notifications")
    .then((module) => {
      module.setNotificationHandler({
        handleNotification: async () => ({
          shouldPlaySound: true,
          shouldSetBadge: false,
          shouldShowAlert: true,
          shouldShowBanner: true,
          shouldShowList: true,
        }),
      });
      return module;
    })
    .catch(() => null);
  return notificationsModulePromise;
}

export async function requestNotificationPermission(): Promise<boolean> {
  const Notifications = await loadNotifications();
  if (!Notifications) return false;
  await ensureNotificationChannel(Notifications);
  if (!Device.isDevice) return false;
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  const next = await Notifications.requestPermissionsAsync();
  return next.granted;
}

export async function notifyApproval(approval: BackendApproval): Promise<void> {
  const Notifications = await loadNotifications();
  if (!Notifications) return;
  await ensureNotificationChannel(Notifications);
  await Notifications.scheduleNotificationAsync({
    content: {
      title: "Lengrvis 需要你审批",
      body: "有任务等待审批，打开 App 查看详情。",
      data: { approvalId: approval.id },
      sound: "default",
      priority: Notifications.AndroidNotificationPriority.HIGH,
    },
    trigger: Platform.OS === "android" ? { channelId: "approvals" } : null,
  });
}

export function addApprovalNotificationResponseListener(
  listener: (approvalId: string) => void,
): NotificationSubscription {
  if (isExpoGoRuntime()) return { remove: () => undefined };
  let innerSubscription: NotificationSubscription | null = null;
  let removed = false;
  void loadNotifications().then((Notifications) => {
    if (!Notifications || removed) return;
    innerSubscription = Notifications.addNotificationResponseReceivedListener((response) => {
      const approvalId = approvalIdFromNotificationData(response.notification.request.content.data);
      if (approvalId) listener(approvalId);
    });
    if (removed) innerSubscription.remove();
  });
  return {
    remove: () => {
      removed = true;
      innerSubscription?.remove();
    },
  };
}

export async function getLastApprovalNotificationApprovalId(): Promise<string | null> {
  try {
    const Notifications = await loadNotifications();
    if (!Notifications) return null;
    const response = Notifications.getLastNotificationResponse();
    const approvalId = approvalIdFromNotificationData(response?.notification.request.content.data);
    if (approvalId) Notifications.clearLastNotificationResponse();
    return approvalId;
  } catch {
    return null;
  }
}

async function ensureNotificationChannel(Notifications: ExpoNotifications): Promise<void> {
  if (Platform.OS !== "android") return;
  await Notifications.setNotificationChannelAsync("approvals", {
    name: "审批提醒",
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: "#0e5f76",
  });
}

function approvalIdFromNotificationData(data: Record<string, unknown> | undefined): string | null {
  const approvalId = data?.approvalId;
  return typeof approvalId === "string" && approvalId ? approvalId : null;
}
