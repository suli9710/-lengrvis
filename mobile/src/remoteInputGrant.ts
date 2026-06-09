import type { RemoteInputGrant } from "./api/client";

const REMOTE_INPUT_SCOPE = "remote:input";

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

export interface RemoteInputGrantDisplayStatus {
  label: string;
  detail: string;
  isActive: boolean;
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

export function remoteInputGrantRemainingText(grant: RemoteInputGrant | null | undefined, now = Date.now()): string {
  if (!grant || !grant.id || remoteInputGrantScope(grant) !== REMOTE_INPUT_SCOPE) return "未授权";
  if (remoteInputGrantStatus(grant) === "expired") return "已过期";
  if (remoteInputGrantStatus(grant) !== "active" || remoteInputGrantRevokedAt(grant)) return "未授权";
  const remainingMs = remoteInputGrantExpiryDelayMs(grant, now);
  if (remainingMs === null || remainingMs <= 0) return "已过期";
  const totalSeconds = Math.ceil(remainingMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes <= 0) return `${seconds} 秒`;
  return `${minutes} 分 ${seconds.toString().padStart(2, "0")} 秒`;
}

export function isRemoteInputGrantUsable(
  grant: RemoteInputGrant | null | undefined,
  now = Date.now(),
): boolean {
  if (!grant) return false;
  if (!grant.id || remoteInputGrantScope(grant) !== REMOTE_INPUT_SCOPE) return false;
  if (remoteInputGrantStatus(grant) !== "active") return false;
  if (remoteInputGrantRevokedAt(grant)) return false;
  const remainingMs = remoteInputGrantExpiryDelayMs(grant, now);
  return remainingMs !== null && remainingMs > 0;
}

export function remoteInputGrantDisplayStatus(
  grant: RemoteInputGrant | null | undefined,
  now = Date.now(),
  options: { locallyRevoked?: boolean } = {},
): RemoteInputGrantDisplayStatus {
  if (isRemoteInputGrantUsable(grant, now)) {
    return {
      label: "已授权输入",
      detail: `可接管输入，剩余 ${remoteInputGrantRemainingText(grant, now)}；点击、文字和按键仍需电脑端审批。`,
      isActive: true,
    };
  }
  if (options.locallyRevoked || remoteInputGrantRevokedAt(grant) || remoteInputGrantStatus(grant) === "revoked") {
    return {
      label: "只读观看",
      detail: "输入授权已结束；屏幕查看仍可用。",
      isActive: false,
    };
  }
  if (remoteInputGrantExpired(grant, now)) {
    return {
      label: "只读观看",
      detail: "输入授权已过期；请在电脑端重新授权。",
      isActive: false,
    };
  }
  return {
    label: "只读观看",
    detail: "电脑端授权前，只能查看屏幕。",
    isActive: false,
  };
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

function remoteInputGrantExpired(grant: RemoteInputGrant | null | undefined, now: number): boolean {
  if (!grant || remoteInputGrantScope(grant) !== REMOTE_INPUT_SCOPE) return false;
  if (remoteInputGrantStatus(grant) === "expired") return true;
  return remoteInputGrantRemainingText(grant, now) === "已过期";
}

function remoteInputGrantScope(grant: RemoteInputGrant | null | undefined): string {
  return String(grant?.scope ?? "").trim().toLowerCase();
}

function remoteInputGrantStatus(grant: RemoteInputGrant | null | undefined): string {
  return String(grant?.status ?? "").trim().toLowerCase();
}

function remoteInputGrantRevokedAt(grant: RemoteInputGrant | null | undefined): string {
  return String(grant?.revoked_at ?? "").trim();
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
