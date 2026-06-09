import type { MobileDevice, RemoteInputGrant } from "./apiClient";

const REMOTE_INPUT_SCOPE = "remote:input";
const ACTIVE_STATUS = "active";

type RemoteInputGrantDeviceBinding = RemoteInputGrant & { device_id?: string };

export function mobileDeviceCanReceiveRemoteInputGrant(device: MobileDevice, remoteDesktopEnabled = true): boolean {
  if (!remoteDesktopEnabled) return false;
  if (!nonEmptyText(device.device_id)) return false;
  if (device.revoked_at?.trim()) return false;
  const status = normalizedStatus(device.status);
  return status === "" || status === ACTIVE_STATUS;
}

export function remoteInputGrantExpiryTime(grant: RemoteInputGrant): number | null {
  if (!grant.expires_at) return null;
  const expiresAt = Date.parse(grant.expires_at);
  return Number.isFinite(expiresAt) ? expiresAt : null;
}

export function isRemoteInputGrantActive(grant: RemoteInputGrant, now = Date.now(), expectedDeviceId?: string): boolean {
  if (!nonEmptyText(grant.id)) return false;
  if (!nonEmptyText(grant.scope)) return false;
  if (normalizedStatus(grant.scope) !== REMOTE_INPUT_SCOPE) return false;
  const grantDeviceId = remoteInputGrantDeviceId(grant);
  if (expectedDeviceId === undefined) {
    if (!grantDeviceId) return false;
  } else {
    const normalizedExpectedDeviceId = normalizedDeviceId(expectedDeviceId);
    if (!normalizedExpectedDeviceId) return false;
    // Device-list grants are nested under the device; explicit bindings still must match.
    if (remoteInputGrantHasDeviceId(grant) && grantDeviceId !== normalizedExpectedDeviceId) return false;
  }
  const status = normalizedStatus(grant.status);
  if (status !== "" && status !== ACTIVE_STATUS) return false;
  if (grant.revoked_at?.trim()) return false;
  const expiresAt = remoteInputGrantExpiryTime(grant);
  return expiresAt !== null && expiresAt > now;
}

export function activeRemoteInputGrantForDevice(device: MobileDevice, now = Date.now(), remoteDesktopEnabled = true): RemoteInputGrant | null {
  if (!mobileDeviceCanReceiveRemoteInputGrant(device, remoteDesktopEnabled)) return null;
  const grants = (device.remote_input_grants ?? []).filter((grant) => isRemoteInputGrantActive(grant, now, device.device_id));
  grants.sort((left, right) => (remoteInputGrantExpiryTime(right) ?? 0) - (remoteInputGrantExpiryTime(left) ?? 0));
  return grants[0] ?? null;
}

function normalizedStatus(value?: string): string {
  return String(value ?? "").trim().toLowerCase();
}

function nonEmptyText(value?: string): boolean {
  return String(value ?? "").trim().length > 0;
}

function normalizedDeviceId(value?: string): string {
  return String(value ?? "").trim();
}

function remoteInputGrantDeviceId(grant: RemoteInputGrant): string {
  return normalizedDeviceId((grant as RemoteInputGrantDeviceBinding).device_id);
}

function remoteInputGrantHasDeviceId(grant: RemoteInputGrant): boolean {
  return Object.prototype.hasOwnProperty.call(grant, "device_id");
}
