import type { ButlerDesktopBridge } from "../../../shared/butlerTypes";
import type { ApiResponse } from "../../../shared/desktopBridgeTypes";
import { safeIpcApiRequest } from "./apiRequestSession";

export function butlerBridgeAvailable(): boolean {
  return typeof window.lengrvis?.butler?.message === "function";
}

/** Butler mutations have no generic HTTP fallback: authorization lives in main. */
export function requestButler<T>(invoke: (bridge: ButlerDesktopBridge) => Promise<ApiResponse<T>>): Promise<ApiResponse<T>> {
  const bridge = window.lengrvis?.butler;
  if (!bridge) return Promise.resolve({
    ok: false, status: 0,
    error: { code: "BUTLER_DESKTOP_REQUIRED", message: "管家功能需要更新后的 Electron 桌面应用。" },
    receivedAt: new Date().toISOString()
  });
  return safeIpcApiRequest(() => invoke(bridge));
}
