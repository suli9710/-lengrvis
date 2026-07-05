// Facade preserving the original lib/apiClient module surface.
// Implementation lives in lib/api/ (transport, client, mappers, backendTypes).
export { FALLBACK_BACKEND_URL, absoluteRendererLoopbackBackendUrl, buildRendererLoopbackBackendApiUrl, buildRendererLoopbackBackendWebSocketUrl, normalizeRendererLoopbackBackendBaseUrl } from "./api/transport";
export { LengrvisApiClient } from "./api/client";
export type { JsonRealtimeHandlers, RealtimeConnectionState, RealtimeConnectionStatus } from "./api/realtimeTransport";
export type { LocalModelInstallRequest, LocalModelInstallResponse, OllamaActionResponse } from "./api/transport";
export type { BrowserReplayExport } from "../../shared/browserTypes";
export type { FileClusterOptions } from "../../shared/fileLibraryTypes";
export type { BackendBrowserSessionStreamEvent } from "./api/browserBackendTypes";
export type { BackendClusterEntry, BackendClusterResponse } from "./api/fileLibraryBackendTypes";
export type { BackendRealtimeStatusEvent, BackendRunStreamEvent, BackendTaskStreamEvent } from "./api/backendTypes";
export type { MobileDevice, MobileDeviceList, MobilePairingCode, RemoteInputGrant, RemoteInputGrantIssueResult } from "./api/mobilePairingBackendTypes";
