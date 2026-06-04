import type { RemoteInputGrant } from "./api/client";

export interface RemoteViewerSize {
  width: number;
  height: number;
}

export interface RemoteFrameGeometry {
  width: number;
  height: number;
  originalWidth: number;
  originalHeight: number;
}

export type RemoteInputGrantStateAction =
  | { type: "received"; grant: RemoteInputGrant }
  | { type: "revoked"; grantId: string }
  | { type: "expired"; grantId: string }
  | { type: "cleared" };

export function remoteInputGrantExpiryDelayMs(grant: RemoteInputGrant, now = Date.now()): number | null {
  if (!grant.expires_at) return null;
  const expiresAt = Date.parse(grant.expires_at);
  if (!Number.isFinite(expiresAt)) return null;
  return Math.max(0, expiresAt - now);
}

export function isRemoteInputGrantUsable(
  grant: RemoteInputGrant | null | undefined,
  now = Date.now(),
): grant is RemoteInputGrant {
  if (!grant) return false;
  if (grant.status !== "active") return false;
  if (grant.revoked_at) return false;
  const remainingMs = remoteInputGrantExpiryDelayMs(grant, now);
  return remainingMs !== null && remainingMs > 0;
}

export function reduceRemoteInputGrant(
  current: RemoteInputGrant | null,
  action: RemoteInputGrantStateAction,
  now = Date.now(),
): RemoteInputGrant | null {
  if (action.type === "received") {
    const grantId = action.grant.id;
    if (isRemoteInputGrantUsable(action.grant, now)) {
      return action.grant;
    }
    return current?.id === grantId ? null : current;
  }
  if (action.type === "revoked" || action.type === "expired") {
    return current?.id === action.grantId ? null : current;
  }
  return null;
}

export function mapViewerPointToRemote(
  x: number,
  y: number,
  viewer: RemoteViewerSize,
  frame: RemoteFrameGeometry,
): { x: number; y: number } | null {
  if (!isPositiveFinite(frame.width) || !isPositiveFinite(frame.height)) return null;
  if (!isPositiveFinite(frame.originalWidth) || !isPositiveFinite(frame.originalHeight)) return null;
  if (!isPositiveFinite(viewer.width) || !isPositiveFinite(viewer.height)) return null;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;

  const imageRatio = frame.width / frame.height;
  const viewerRatio = viewer.width / viewer.height;
  const renderedWidth = viewerRatio > imageRatio ? viewer.height * imageRatio : viewer.width;
  const renderedHeight = viewerRatio > imageRatio ? viewer.height : viewer.width / imageRatio;
  const offsetX = (viewer.width - renderedWidth) / 2;
  const offsetY = (viewer.height - renderedHeight) / 2;
  const localX = x - offsetX;
  const localY = y - offsetY;
  if (localX < 0 || localY < 0 || localX > renderedWidth || localY > renderedHeight) return null;

  return {
    x: clamp(Math.floor((localX / renderedWidth) * frame.originalWidth), 0, frame.originalWidth - 1),
    y: clamp(Math.floor((localY / renderedHeight) * frame.originalHeight), 0, frame.originalHeight - 1),
  };
}

function isPositiveFinite(value: number): boolean {
  return Number.isFinite(value) && value > 0;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
