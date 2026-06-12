// Facade preserving the original lib/apiClient module surface.
// Implementation lives in lib/api/ (transport, client, mappers, backendTypes).
export { FALLBACK_BACKEND_URL, absoluteRendererLoopbackBackendUrl, buildRendererLoopbackBackendApiUrl, buildRendererLoopbackBackendWebSocketUrl, normalizeRendererLoopbackBackendBaseUrl } from "./api/transport";
export { LengrvisApiClient } from "./api/client";
export type { JsonRealtimeHandlers, LocalModelInstallRequest, LocalModelInstallResponse, OllamaActionResponse, RealtimeConnectionState, RealtimeConnectionStatus } from "./api/transport";
export type { BackendBrowserSessionStreamEvent, BackendClusterEntry, BackendClusterResponse, BackendRealtimeStatusEvent, BackendRunStreamEvent, BackendTaskStreamEvent, BrowserReplayExport, FileClusterOptions, MobileDevice, MobileDeviceList, MobilePairingCode, RemoteInputGrant, RemoteInputGrantIssueResult } from "./api/backendTypes";
