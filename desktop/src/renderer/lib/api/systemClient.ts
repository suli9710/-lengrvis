import type { InstalledApp } from "../../../shared/catalogTypes";
import type {
  ApiRequest,
  ApiResponse,
  DesktopPrivacyEraseRequest,
  DesktopPrivacyEraseResponse
} from "../../../shared/desktopBridgeTypes";
import type { DiagnosticExportResult, LocalMetricsSummary, SystemInfo } from "../../../shared/systemTypes";
import type { AuditLogEntry, PrivacyEraseResult } from "../../../shared/types";
import type { BackendAuditEvent } from "./backendTypes";
import type { BackendAppsResponse } from "./catalogBackendTypes";
import { mapInstalledApp } from "./catalogMappers";
import { mapDiagnostic, mapDiagnosticExportResult, mapLocalMetrics, mapProcess, mapStartupItem } from "./diagnosticMappers";
import type {
  BackendDiagnosticExportResult,
  BackendLocalMetrics,
  BackendProcessesResponse,
  BackendStartupResponse,
  BackendSystemDiagnostics,
  BackendSystemInfo
} from "./systemBackendTypes";
import { FALLBACK_BACKEND_URL, mapResponse } from "./transport";

export type SystemEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function getLocalMetricsEndpoint(
  request: SystemEndpointRequest,
  days = 7
): Promise<ApiResponse<LocalMetricsSummary>> {
  return request<BackendLocalMetrics>({
    endpoint: "/api/metrics/local",
    query: { days },
    timeoutMs: 10_000
  }).then((response) => mapResponse(response, (data) => mapLocalMetrics(data, days)));
}

export function listAuditLogsEndpoint(
  request: SystemEndpointRequest
): Promise<ApiResponse<AuditLogEntry[]>> {
  return request<BackendAuditEvent[]>({ endpoint: "/api/audit" }).then((response) =>
    mapResponse(response, (events) =>
      events.map((event) => ({
        id: event.id,
        actor: event.actor,
        action: event.event_type,
        target: event.task_id ?? "系统",
        level: event.event_type.includes("failed") ? "error" : event.event_type.includes("review") ? "warning" : "info",
        createdAt: event.created_at
      }))
    )
  );
}

export function getSystemInfoEndpoint(
  request: SystemEndpointRequest
): Promise<ApiResponse<SystemInfo>> {
  return Promise.all([
    request<BackendSystemInfo>({ endpoint: "/api/system/info" }),
    request<BackendSystemDiagnostics>({ endpoint: "/api/system/diagnostics" }),
    request<BackendProcessesResponse>({ endpoint: "/api/system/processes", query: { limit: 8 } }),
    request<BackendStartupResponse>({ endpoint: "/api/system/startup-items" }),
    request<BackendAppsResponse>({ endpoint: "/api/apps" })
  ]).then(([infoResponse, diagnosticsResponse, processesResponse, startupResponse, appsResponse]) =>
    mapResponse(infoResponse, (info) => ({
      appVersion: window.lengrvis?.versions.app ?? "0.1.1",
      electronVersion: window.lengrvis?.versions.electron ?? "未知",
      chromeVersion: window.lengrvis?.versions.chrome ?? "未知",
      nodeVersion: window.lengrvis?.versions.node ?? "未知",
      platform: info.system ?? info.platform ?? window.lengrvis?.platform ?? "未知",
      arch: info.machine ?? "未知",
      backendBaseUrl: window.lengrvis?.backendBaseUrl ?? FALLBACK_BACKEND_URL,
      diagnostics: diagnosticsResponse.ok && diagnosticsResponse.data
        ? mapDiagnostic(diagnosticsResponse.data, startupResponse.data?.startup_items)
        : undefined,
      processes: processesResponse.ok && processesResponse.data
        ? processesResponse.data.processes.map(mapProcess)
        : undefined,
      startupItems: startupResponse.ok && startupResponse.data
        ? startupResponse.data.startup_items.map(mapStartupItem)
        : undefined,
      installedApps: appsResponse.ok && appsResponse.data
        ? appsResponse.data.apps.map(mapInstalledApp)
        : undefined
    }))
  );
}

export function exportDiagnosticsPackageEndpoint(
  request: SystemEndpointRequest
): Promise<ApiResponse<DiagnosticExportResult>> {
  const diagnosticRequest = window.lengrvis?.system
    ? window.lengrvis.system.exportDiagnosticsPackage() as Promise<ApiResponse<BackendDiagnosticExportResult>>
    : request<BackendDiagnosticExportResult>({
        endpoint: "/api/system/diagnostics/export",
        method: "POST",
        timeoutMs: 10_000
      });
  return diagnosticRequest.then((response) => mapResponse(response, mapDiagnosticExportResult));
}

export function eraseLocalDataEndpoint(
  request: DesktopPrivacyEraseRequest
): Promise<ApiResponse<PrivacyEraseResult>> {
  if (!window.lengrvis?.privacy) {
    return Promise.resolve({
      ok: false,
      status: 0,
      error: {
        code: "DESKTOP_REQUIRED",
        message: "删除本机数据需要在 Electron 桌面应用中完成"
      },
      receivedAt: new Date().toISOString()
    });
  }
  return window.lengrvis.privacy
    .eraseLocalData(request)
    .then((response: ApiResponse<DesktopPrivacyEraseResponse>) =>
      mapResponse(response, (data) => ({
        scope: data.scope,
        deletedRowsByTable: data.deleted.rows_by_table,
        deletedRowsTotal: Number(data.deleted.rows_total || 0),
        deletedDiagnosticPackages: Number(data.deleted.diagnostic_packages || 0),
        preserved: Array.isArray(data.preserved) ? data.preserved.map(String) : [],
        settingsReset: !data.preserved.includes("app_settings"),
        manualLogCleanupRequired: data.manual_cleanup?.log_dirs === "not_deleted_at_runtime_see_settings_system_info",
        auditRecorded: data.audit === "erase_event_appended_to_local_audit_chain"
      }))
    );
}

export function listAppsEndpoint(
  request: SystemEndpointRequest
): Promise<ApiResponse<InstalledApp[]>> {
  return request<BackendAppsResponse>({ endpoint: "/api/apps" }).then((response) =>
    mapResponse(response, (data) => data.apps.map(mapInstalledApp))
  );
}

export function openWindowsSettingsEndpoint(
  request: SystemEndpointRequest,
  uri: string
): Promise<ApiResponse<{ ok: boolean; uri: string; opened?: boolean; error?: string }>> {
  if (window.lengrvis?.system.openSettings) {
    return window.lengrvis.system.openSettings({ uri }) as Promise<
      ApiResponse<{ ok: boolean; uri: string; opened?: boolean; error?: string }>
    >;
  }
  return request<{ ok: boolean; uri: string; opened?: boolean; error?: string }, { uri: string }>({
    endpoint: "/api/system/open-settings",
    method: "POST",
    body: { uri }
  });
}
