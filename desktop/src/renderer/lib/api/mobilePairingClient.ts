import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  MobileDevice,
  MobileDeviceList,
  MobilePairingCode,
  RemoteInputGrant,
  RemoteInputGrantIssueResult
} from "./mobilePairingBackendTypes";

export type MobilePairingEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function createMobilePairingCodeEndpoint(
  request: MobilePairingEndpointRequest
): Promise<ApiResponse<MobilePairingCode>> {
  if (window.lengrvis?.mobilePairing) {
    return window.lengrvis.mobilePairing.createCode() as Promise<ApiResponse<MobilePairingCode>>;
  }
  return request<MobilePairingCode>({ endpoint: "/api/pair/request", method: "POST" });
}

export function listMobileDevicesEndpoint(
  request: MobilePairingEndpointRequest
): Promise<ApiResponse<MobileDeviceList>> {
  if (window.lengrvis?.mobilePairing) {
    return window.lengrvis.mobilePairing.listDevices() as Promise<ApiResponse<MobileDeviceList>>;
  }
  return request<MobileDeviceList>({ endpoint: "/api/pair/devices" });
}

export function revokeMobileDeviceEndpoint(
  request: MobilePairingEndpointRequest,
  deviceId: string
): Promise<ApiResponse<MobileDevice>> {
  if (window.lengrvis?.mobilePairing) {
    return window.lengrvis.mobilePairing.revokeDevice(deviceId) as Promise<ApiResponse<MobileDevice>>;
  }
  return request<MobileDevice>({
    endpoint: `/api/pair/devices/${encodeURIComponent(deviceId)}`,
    method: "DELETE"
  });
}

export function createRemoteInputGrantEndpoint(
  request: MobilePairingEndpointRequest,
  deviceId: string,
  expiresInSeconds = 300
): Promise<ApiResponse<RemoteInputGrantIssueResult>> {
  if (window.lengrvis?.mobilePairing) {
    return window.lengrvis.mobilePairing.createRemoteInputGrant({
      deviceId,
      expiresInSeconds
    }) as Promise<ApiResponse<RemoteInputGrantIssueResult>>;
  }
  return request<RemoteInputGrantIssueResult, { expires_in: number }>({
    endpoint: `/api/pair/devices/${encodeURIComponent(deviceId)}/remote-input-grants`,
    method: "POST",
    body: { expires_in: expiresInSeconds }
  });
}

export function revokeRemoteInputGrantEndpoint(
  request: MobilePairingEndpointRequest,
  deviceId: string,
  grantId: string
): Promise<ApiResponse<RemoteInputGrant>> {
  if (window.lengrvis?.mobilePairing) {
    return window.lengrvis.mobilePairing.revokeRemoteInputGrant({
      deviceId,
      grantId
    }) as Promise<ApiResponse<RemoteInputGrant>>;
  }
  return request<RemoteInputGrant>({
    endpoint: `/api/pair/devices/${encodeURIComponent(deviceId)}/remote-input-grants/${encodeURIComponent(grantId)}`,
    method: "DELETE"
  });
}
