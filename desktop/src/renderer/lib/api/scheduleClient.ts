import type {
  ApiRequest,
  ApiResponse,
  DesktopRunMode
} from "../../../shared/desktopBridgeTypes";
import { safeIpcApiRequest } from "./apiRequestSession";
import type { BackendScheduledTask } from "./scheduleBackendTypes";

export interface ScheduleInput {
  cron: string;
  goal: string;
  mode: DesktopRunMode;
  note?: string;
}

export type ScheduleEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function listSchedulesEndpoint(
  request: ScheduleEndpointRequest
): Promise<ApiResponse<BackendScheduledTask[]>> {
  if (window.lengrvis?.schedules) {
    return safeIpcApiRequest(() =>
      window.lengrvis.schedules.list() as Promise<ApiResponse<BackendScheduledTask[]>>
    );
  }
  return request<BackendScheduledTask[]>({ endpoint: "/api/schedules" });
}

export function createScheduleEndpoint(
  request: ScheduleEndpointRequest,
  input: ScheduleInput
): Promise<ApiResponse<BackendScheduledTask>> {
  if (window.lengrvis?.schedules) {
    return safeIpcApiRequest(() =>
      window.lengrvis.schedules.create(input) as Promise<ApiResponse<BackendScheduledTask>>
    );
  }
  return request<BackendScheduledTask, ScheduleInput>({
    endpoint: "/api/schedules",
    method: "POST",
    body: input
  });
}

export function deleteScheduleEndpoint(
  request: ScheduleEndpointRequest,
  scheduleId: string
): Promise<ApiResponse<{ ok: boolean; id: string }>> {
  if (window.lengrvis?.schedules) {
    return safeIpcApiRequest(() =>
      window.lengrvis.schedules.delete(scheduleId) as Promise<ApiResponse<{ ok: boolean; id: string }>>
    );
  }
  return request({
    endpoint: `/api/schedules/${scheduleId}`,
    method: "DELETE"
  });
}

export function enableScheduleEndpoint(
  request: ScheduleEndpointRequest,
  scheduleId: string,
  enabled: boolean
): Promise<ApiResponse<BackendScheduledTask>> {
  if (window.lengrvis?.schedules) {
    return safeIpcApiRequest(() =>
      window.lengrvis.schedules.enable({ scheduleId, enabled }) as Promise<ApiResponse<BackendScheduledTask>>
    );
  }
  return request<BackendScheduledTask, { enabled: boolean }>({
    endpoint: `/api/schedules/${scheduleId}/enable`,
    method: "POST",
    body: { enabled }
  });
}
