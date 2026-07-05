import type { BaseUrlSecurity, PairingSession } from "../api/client";
import type { RemoteInputGrantDisplayStatus } from "../remoteInputGrant";

export type ConnectionState = "offline" | "connecting" | "online" | "paused";
export type InputConnectionState = "disabled" | "ready" | "connecting" | "online" | "offline";
export type RemoteViewerZoom = "fit" | "close" | "detail";

export interface TransportNotice {
  tone: "secure" | "warning" | "danger";
  title: string;
  detail: string;
  warning?: string;
}

export const FRAME_ACK_FALLBACK_MS = 900;
export const INITIAL_SCREEN_RECONNECT_DELAY_MS = 1000;
export const MAX_SCREEN_RECONNECT_DELAY_MS = 15000;
export const REMOTE_VIEWER_PADDING_HORIZONTAL = 12;
export const REMOTE_VIEWER_PADDING_VERTICAL = 18;
export const REMOTE_TEXT_INPUT_MAX_LENGTH = 180;
export const REMOTE_VIEWER_ZOOM_OPTIONS: Array<{ value: RemoteViewerZoom; label: string; factor: number }> = [
  { value: "fit", label: "适应", factor: 1 },
  { value: "close", label: "放大", factor: 1.35 },
  { value: "detail", label: "细节", factor: 1.75 },
];
export const REMOTE_KEY_CONTROLS: Array<{ key: string; label: string; icon?: "enter" | "delete" | "pageup" | "pagedown" }> = [
  { key: "enter", label: "回车", icon: "enter" },
  { key: "escape", label: "Esc" },
  { key: "tab", label: "Tab" },
  { key: "backspace", label: "退格", icon: "delete" },
  { key: "pageup", label: "上翻", icon: "pageup" },
  { key: "pagedown", label: "下翻", icon: "pagedown" },
];

export function statusText(connection: ConnectionState): string {
  if (connection === "online") return "实时";
  if (connection === "connecting") return "连接中";
  if (connection === "paused") return "已暂停";
  return "离线";
}

export function streamStatusMetaText(meta: { fps: number; quality: number }, connection: ConnectionState): string {
  if (connection !== "online" || !meta.fps) return "";
  if (meta.fps <= 1 || meta.quality <= 42) return `低带宽 ${meta.fps} FPS`;
  return `${meta.fps} FPS`;
}

export function screenReconnectStatusText(connection: ConnectionState, nextReconnectAtMs: number | null, nowMs: number): string {
  if (connection !== "offline" || nextReconnectAtMs === null) return "";
  const remainingSeconds = Math.max(1, Math.ceil((nextReconnectAtMs - nowMs) / 1000));
  return `自动重连 ${remainingSeconds} 秒`;
}

export function viewerConnectionText(connection: ConnectionState): string {
  if (connection === "online") return "实时画面";
  if (connection === "connecting") return "正在连接";
  if (connection === "paused") return "已暂停";
  return "离线";
}

export function viewerInputBadgeText(grantUsable: boolean, connection: InputConnectionState): string {
  if (!grantUsable) return "只读屏幕查看";
  if (connection === "online") return "已授权输入";
  if (connection === "connecting") return "输入连接中";
  if (connection === "offline") return "输入连接失败";
  return "已授权，待连接";
}

export function viewerHintText({
  connection,
  frameAvailable,
  grantUsable,
  transportBlocked,
}: {
  connection: ConnectionState;
  frameAvailable: boolean;
  grantUsable: boolean;
  transportBlocked: boolean;
}): string {
  if (transportBlocked) return "安全连接开启前，手机不会显示屏幕或发送远程输入";
  if (!frameAvailable) return "等待电脑端发送屏幕画面";
  if (connection !== "online") return "恢复实时屏幕后才能发送远程输入";
  if (!grantUsable) return "电脑端授权输入前只能查看屏幕";
  return "轻点屏幕发送点击，也可以发送文字或常用按键；电脑端批准后才会执行";
}

export function fitRemoteViewerSurface(
  container: { width: number; height: number },
  aspectRatio: number,
): { width: number; height: number } | null {
  if (!Number.isFinite(container.width) || !Number.isFinite(container.height)) return null;
  if (container.width <= 0 || container.height <= 0 || !Number.isFinite(aspectRatio) || aspectRatio <= 0) return null;
  const containerRatio = container.width / container.height;
  if (containerRatio > aspectRatio) {
    const height = container.height;
    return { width: height * aspectRatio, height };
  }
  const width = container.width;
  return { width, height: width / aspectRatio };
}

export function zoomRemoteViewerSurface(
  surface: { width: number; height: number } | null,
  zoomFactor: number,
): { width: number; height: number } | null {
  if (!surface) return null;
  const factor = Number.isFinite(zoomFactor) && zoomFactor > 0 ? zoomFactor : 1;
  return {
    width: surface.width * factor,
    height: surface.height * factor,
  };
}

export function finiteIntegerOrZero(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : 0;
}

export function readableStreamConnectionError(error: unknown): string {
  const message = error instanceof Error ? error.message.trim().toLowerCase() : "";
  if (
    message.includes("非本机 http") ||
    message.includes("http") ||
    message.includes("明文") ||
    message.includes("insecure")
  ) {
    return "当前网络连接不够安全，手机不会继续查看屏幕。请在电脑端开启安全连接后重新配对。";
  }
  return "暂时无法显示屏幕。请确认 Lengrvis 已打开，然后点重试。";
}

export function readableStreamError(message: string): string {
  const normalized = message.trim().toLowerCase();
  if (!normalized) return "暂时无法显示屏幕。请点重试重新连接。";
  if (normalized.includes("disabled")) return "桌面端尚未开启远程屏幕。请在 Lengrvis 设置中打开手机屏幕查看。";
  if (normalized.includes("unauthorized") || normalized.includes("token") || normalized.includes("scope")) {
    return "这台手机没有屏幕查看权限。请在桌面端重新配对后再试。";
  }
  return "暂时无法显示屏幕。请点重试重新连接。";
}

export function inputStatusText(connection: InputConnectionState): string {
  if (connection === "online") return "已授权输入：点击、文字和按键仍需电脑端审批";
  if (connection === "connecting") return "正在启用已授权输入";
  if (connection === "ready") return "电脑端已授权输入，点击连接后可使用";
  if (connection === "offline") return "已授权输入连接失败，请重试或在电脑端重新授权";
  return "只读观看：电脑端尚未授权输入";
}

export function inputErrorMessage(error: unknown): string {
  return readableInputFailureReason(error);
}

export function remoteInputModeText(grantUsable: boolean, connection: InputConnectionState): string {
  if (!grantUsable) return "只读观看";
  if (connection === "online") return "已授权输入";
  if (connection === "connecting") return "正在启用输入";
  return "已授权输入，待连接";
}

export function remoteInputGrantStatusMeta({
  grantDisplayStatus,
  grantRemainingText,
  grantUsable,
}: {
  grantDisplayStatus: RemoteInputGrantDisplayStatus;
  grantRemainingText: string;
  grantUsable: boolean;
}): string {
  if (grantUsable) return `授权剩余 ${grantRemainingText}；点击、文字和按键仍需电脑端审批`;
  return grantDisplayStatus.detail;
}

export function readableInputFailureReason(errorOrMessage: unknown, eventType?: string): string {
  if (eventType === "denied") return "电脑端已结束或拒绝这次远程输入。";
  const message = typeof errorOrMessage === "string"
    ? errorOrMessage
    : errorOrMessage instanceof Error
      ? errorOrMessage.message
      : "";
  const normalized = message.trim().toLowerCase();
  if (!normalized) return "远程输入暂时不可用。请在电脑端重新授权后重试。";
  if (normalized.includes("非本机 http") || normalized.includes("明文") || normalized.includes("insecure lan")) {
    return "当前网络连接不够安全，无法发送远程输入。请在电脑端开启安全连接后重新配对。";
  }
  if (normalized.includes("expired") || normalized.includes("410")) return "输入授权已过期。请在电脑端重新授权。";
  if (normalized.includes("revoked") || normalized.includes("denied")) return "电脑端已结束或拒绝这次远程输入。";
  if (normalized.includes("unauthorized") || normalized.includes("forbidden") || normalized.includes("token") || normalized.includes("scope")) {
    return "这台手机没有远程输入权限。请在电脑端重新授权。";
  }
  if (normalized.includes("fetch") || normalized.includes("network") || normalized.includes("failed") || normalized.includes("timeout")) {
    return "远程输入连接失败。请确认电脑端在线后重试。";
  }
  return "远程输入暂时不可用。请在电脑端重新授权后重试。";
}

export function inputFeedbackIsWarning(message: string): boolean {
  return /失败|过期|拒绝|没有|不可用|不够安全|等待屏幕|不在屏幕/.test(message);
}

export function webSocketCloseLooksSessionExpired(event: { code?: number; reason?: string }, session?: Pick<PairingSession, "token" | "expiresAt">): boolean {
  if (pairingSessionTokenIsMissingOrExpired(session)) return true;
  if (event.code !== 1008 && event.code !== 4001 && event.code !== 4401) return false;
  return messageLooksSessionExpired(event.reason);
}

export function webSocketCloseLooksRemoteInputGrantEnded(event: { code?: number; reason?: string }, hasActiveGrant = false): boolean {
  if (event.code === 410) return true;
  if (event.code !== 1008 && event.code !== 410 && event.code !== 4403) return false;
  if (hasActiveGrant && event.code === 1008) return true;
  return remoteInputGrantFailureIsTerminal(event.reason);
}

export function messageLooksSessionExpired(message: unknown): boolean {
  const normalized = normalizedMessage(message);
  if (!normalized) return false;
  const mentionsSession =
    normalized.includes("session") ||
    normalized.includes("mobile token") ||
    normalized.includes("pairing token") ||
    normalized.includes("mobile device");
  const mentionsExpiredAuth =
    normalized.includes("expired") ||
    normalized.includes("unauthorized") ||
    normalized.includes("auth") ||
    normalized.includes("missing") ||
    normalized.includes("revoked") ||
    normalized.includes("not paired") ||
    normalized.includes("inactive");
  return mentionsSession && mentionsExpiredAuth;
}

export function remoteInputGrantFailureIsTerminal(message: unknown): boolean {
  const normalized = normalizedMessage(message);
  if (!normalized) return false;
  const mentionsGrant = normalized.includes("grant") || normalized.includes("remote input");
  return (
    mentionsGrant &&
    (
      normalized.includes("expired") ||
      normalized.includes("revoked") ||
      normalized.includes("not active") ||
      normalized.includes("inactive") ||
      normalized.includes("invalid") ||
      normalized.includes("missing") ||
      normalized.includes("required") ||
      normalized.includes("410") ||
      normalized.includes("gone")
    )
  );
}

function normalizedMessage(message: unknown): string {
  if (typeof message === "string") return message.trim().toLowerCase();
  if (message instanceof Error) return message.message.trim().toLowerCase();
  return "";
}

function pairingSessionTokenIsMissingOrExpired(session?: Pick<PairingSession, "token" | "expiresAt">): boolean {
  if (!session) return false;
  if (!session.token?.trim()) return true;
  if (!session.expiresAt) return false;
  const expiresAt = Date.parse(session.expiresAt);
  return !Number.isFinite(expiresAt) || expiresAt <= Date.now() + 1000;
}

export function remoteTransportNotice(security: BaseUrlSecurity): TransportNotice {
  if (security.isInsecureLan || (!security.isLoopback && (!security.backendTlsEnabled || security.webSocketProtocol !== "wss:"))) {
    return {
      tone: "danger",
      title: "连接已阻止",
      detail: "当前网络连接不够安全，手机不会继续查看屏幕或发送远程输入。",
      warning: "请在电脑端开启安全连接后重新配对。",
    };
  }
  if (security.requiresTlsTrust) {
    return {
      tone: "warning",
      title: "需要确认这台电脑",
      detail: "首次安全连接需要你在电脑端确认。确认前请保持只读观看，不要处理不认识的请求。",
      warning: "如果这不是你正在使用的电脑，请返回并重新配对。",
    };
  }
  if (security.isHttps) {
    return {
      tone: "secure",
      title: "安全连接已开启",
      detail: "屏幕查看和远程输入会通过安全连接发送。",
    };
  }
  return {
    tone: "warning",
    title: "仅限本机调试连接",
    detail: "当前连接只适合本机测试；实际使用请在电脑端生成安全配对信息。",
  };
}
