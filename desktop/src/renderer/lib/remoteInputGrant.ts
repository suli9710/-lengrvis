import type { MobileDevice, RemoteInputGrant } from "./apiClient";

export function mobileDeviceCanReceiveRemoteInputGrant(device: MobileDevice, remoteDesktopEnabled = true): boolean {
  return remoteDesktopEnabled && !(device.revoked_at || device.status === "revoked");
}

export function remoteInputGrantExpiryTime(grant: RemoteInputGrant): number | null {
  if (!grant.expires_at) return null;
  const expiresAt = Date.parse(grant.expires_at);
  return Number.isFinite(expiresAt) ? expiresAt : null;
}

export function isRemoteInputGrantActive(grant: RemoteInputGrant, now = Date.now()): boolean {
  if ((grant.status ?? "active") !== "active") return false;
  if (grant.revoked_at) return false;
  const expiresAt = remoteInputGrantExpiryTime(grant);
  return expiresAt !== null && expiresAt > now;
}

export function activeRemoteInputGrantForDevice(device: MobileDevice, now = Date.now()): RemoteInputGrant | null {
  const grants = (device.remote_input_grants ?? []).filter((grant) => isRemoteInputGrantActive(grant, now));
  grants.sort((left, right) => (remoteInputGrantExpiryTime(right) ?? 0) - (remoteInputGrantExpiryTime(left) ?? 0));
  return grants[0] ?? null;
}
