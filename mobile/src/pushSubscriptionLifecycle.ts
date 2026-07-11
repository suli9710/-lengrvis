import type { MobilePushSubscription, PairingSession } from "./api/client";

const PUSH_REGISTRATION_RETRY_BASE_MS = 60_000;
const PUSH_REGISTRATION_RETRY_MAX_MS = 15 * 60_000;

export interface ApprovalPushSubscriptionDependencies {
  requestSubscription: () => Promise<MobilePushSubscription | null>;
  registerSubscription: (
    session: PairingSession,
    subscription: MobilePushSubscription,
  ) => Promise<void>;
  unregisterSubscription: (session: PairingSession) => Promise<void>;
}

export type ApprovalPushSubscriptionResult =
  | { status: "registered" | "already_registered"; registrationKey: string }
  | { status: "unavailable" };

/**
 * Obtains a device push token only after the native permission flow and binds
 * it to the already-paired desktop session. The caller keeps the returned key
 * to avoid re-registering an unchanged Expo token after a React re-render.
 */
export async function ensureApprovalPushSubscription(
  session: PairingSession,
  dependencies: ApprovalPushSubscriptionDependencies,
  registeredKey = "",
): Promise<ApprovalPushSubscriptionResult> {
  const subscription = await dependencies.requestSubscription();
  if (!subscription) {
    // A token may have been registered by an earlier process lifetime. Make
    // revoking OS notification permission also remove the paired computer's
    // persisted subscription instead of continuing blind deliveries.
    await dependencies.unregisterSubscription(session);
    return { status: "unavailable" };
  }

  const registrationKey = approvalPushSubscriptionRegistrationKey(session, subscription);
  if (registrationKey === registeredKey) {
    return { status: "already_registered", registrationKey };
  }

  await dependencies.registerSubscription(session, subscription);
  return { status: "registered", registrationKey };
}

export function approvalPushSubscriptionRegistrationKey(
  session: Pick<PairingSession, "deviceId">,
  subscription: MobilePushSubscription,
): string {
  return JSON.stringify([session.deviceId, subscription.provider, subscription.token]);
}

export function approvalPushRegistrationRetryDelayMs(failureCount: number): number {
  const normalizedCount = Math.max(0, Math.floor(failureCount));
  return Math.min(
    PUSH_REGISTRATION_RETRY_MAX_MS,
    PUSH_REGISTRATION_RETRY_BASE_MS * 2 ** normalizedCount,
  );
}
