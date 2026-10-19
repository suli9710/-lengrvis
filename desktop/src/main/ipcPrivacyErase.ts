import type { ApiResponse, DesktopPrivacyEraseResponse } from "../shared/types";
import type { InternalDesktopBridgeRequest } from "./ipcApiProxy";

const ERASE_ENDPOINT = "/api/system/privacy/erase-local-data";
const RESERVATION_TOKEN_HEADER = "X-Lengrvis-Erase-Reservation-Token";

export interface LocalPrivacyEraser {
  beginLocalPrivateDataErase: () => boolean;
  eraseLocalPrivateData: () => Promise<void>;
  finishLocalPrivateDataErase: () => void;
}

interface EraseReservation {
  operation_id: string;
  reservation_token: string;
  backend: string;
  desktop: string;
}

type RequestBridge = <TData>(request: InternalDesktopBridgeRequest) => Promise<ApiResponse<TData>>;

export async function performCoordinatedPrivacyErase(options: {
  body: { confirm: string; include_settings: boolean };
  request: RequestBridge;
  confirmationHeaders: () => Promise<Record<string, string>>;
  eraser: LocalPrivacyEraser;
}): Promise<ApiResponse<DesktopPrivacyEraseResponse>> {
  const { request, body, eraser } = options;
  // A persisted desktop marker survives a process crash. Never release a
  // previously pending erase merely because a new preparation was rejected.
  const wasPending = eraser.beginLocalPrivateDataErase();
  let prepareSent = false;
  let reservation: EraseReservation | undefined;
  let desktopComplete = false;
  let backendComplete = false;
  try {
    const headers = await options.confirmationHeaders();
    prepareSent = true;
    const prepared = await request<EraseReservation>({ endpoint: `${ERASE_ENDPOINT}/prepare`, method: "POST", body, headers });
    if (!prepared.ok || !prepared.data) {
      // These statuses unambiguously reject preparation before a reservation
      // can be established. Unknown/network outcomes retain the local gate.
      if (!wasPending && [400, 403, 409, 422, 429].includes(prepared.status)) eraser.finishLocalPrivateDataErase();
      return prepared as unknown as ApiResponse<DesktopPrivacyEraseResponse>;
    }
    reservation = prepared.data;
    if (!reservation.operation_id || !reservation.reservation_token) throw new Error("Invalid erase reservation");
    const reservationHeaders = { [RESERVATION_TOKEN_HEADER]: reservation.reservation_token };
    const checkpoint = async (complete: boolean) => request<DesktopPrivacyEraseResponse>({
      endpoint: `${ERASE_ENDPOINT}/complete`, method: "POST", headers: reservationHeaders,
      body: { operation_id: reservation!.operation_id, desktop_complete: complete }
    });
    try {
      await eraser.eraseLocalPrivateData();
      desktopComplete = true;
    } catch (error) { // broad-exception-boundary: record desktop partial cleanup before propagating a retryable result.
      await checkpoint(false);
      throw error;
    }
    // Persist the desktop phase before attempting the backend phase, even if
    // the next HTTP reply is lost. The same reservation can resume on retry.
    const desktopCheckpoint = await checkpoint(true);
    if (!desktopCheckpoint.ok) return incomplete(desktopComplete, backendComplete, reservation.operation_id);
    const erased = await request<EraseReservation>({
      endpoint: ERASE_ENDPOINT, method: "POST", headers: reservationHeaders,
      body: { ...body, operation_id: reservation.operation_id }
    });
    if (!erased.ok || erased.data?.backend !== "complete") {
      return incomplete(desktopComplete, backendComplete, reservation.operation_id);
    }
    backendComplete = true;
    const completed = await checkpoint(true);
    if (!completed.ok || !completed.data?.ok) return incomplete(desktopComplete, backendComplete, reservation.operation_id);
    eraser.finishLocalPrivateDataErase();
    return completed;
  } catch (error) { // broad-exception-boundary: uncertain HTTP or storage outcomes must keep the durable gate closed for retry.
    if (!prepareSent && !wasPending) {
      eraser.finishLocalPrivateDataErase();
      throw error;
    }
    return incomplete(desktopComplete, backendComplete, reservation?.operation_id);
  }
}

function incomplete(desktop: boolean, backend: boolean, operationId?: string): ApiResponse<DesktopPrivacyEraseResponse> {
  return {
    ok: false, status: 409, receivedAt: new Date().toISOString(),
    error: {
      code: "LOCAL_DATA_ERASE_INCOMPLETE",
      message: `本机数据清除尚未完成（桌面：${desktop ? "已删除" : "待完成"}；后端：${backend ? "已删除" : "待完成"}）。清除预留已保留，请使用相同设置范围重新确认并重试。`,
      details: { operation_id: operationId, desktop_complete: desktop, backend_complete: backend }
    }
  };
}
