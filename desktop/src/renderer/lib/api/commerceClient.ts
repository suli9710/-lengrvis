import type { CommerceLicenseStatus, CommercePlanStatus, CommerceQuotaStatus } from "../../../shared/commerceTypes";
import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  BackendCommerceLicenseStatus,
  BackendCommercePlanStatus,
  BackendCommerceQuotaStatus
} from "./commerceBackendTypes";
import { mapCommerceLicenseStatus, mapCommercePlanStatus, mapCommerceQuotaStatus } from "./commerceMappers";
import { mapResponse } from "./transport";

export type CommerceEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function getCommercePlanEndpoint(
  request: CommerceEndpointRequest
): Promise<ApiResponse<CommercePlanStatus>> {
  return request<BackendCommercePlanStatus>({ endpoint: "/api/commerce/plan" }).then((response) =>
    mapResponse(response, mapCommercePlanStatus)
  );
}

export function getCommerceLicenseEndpoint(
  request: CommerceEndpointRequest
): Promise<ApiResponse<CommerceLicenseStatus>> {
  return request<BackendCommerceLicenseStatus>({ endpoint: "/api/commerce/license" }).then((response) =>
    mapResponse(response, mapCommerceLicenseStatus)
  );
}

export function getCommerceQuotaEndpoint(
  request: CommerceEndpointRequest
): Promise<ApiResponse<CommerceQuotaStatus>> {
  return request<BackendCommerceQuotaStatus>({ endpoint: "/api/commerce/usage/quota" }).then((response) =>
    mapResponse(response, mapCommerceQuotaStatus)
  );
}

export async function installCommerceLicenseEndpoint(
  request: CommerceEndpointRequest,
  token: string
): Promise<ApiResponse<CommerceLicenseStatus>> {
  const response = window.lengrvis?.commerce
    ? await window.lengrvis.commerce.installLicense({ token }) as ApiResponse<BackendCommerceLicenseStatus>
    : await request<BackendCommerceLicenseStatus, { token: string }>({
        endpoint: "/api/commerce/license/install",
        method: "POST",
        body: { token }
      });
  return mapResponse(response, mapCommerceLicenseStatus);
}

export async function activateCommerceLicenseEndpoint(
  request: CommerceEndpointRequest,
  activationKey: string,
  appVersion = "desktop"
): Promise<ApiResponse<CommerceLicenseStatus>> {
  const response = window.lengrvis?.commerce
    ? await window.lengrvis.commerce.activateLicense({ activationKey, appVersion }) as ApiResponse<BackendCommerceLicenseStatus>
    : await request<BackendCommerceLicenseStatus, { activation_key: string; app_version: string }>({
        endpoint: "/api/commerce/license/activate",
        method: "POST",
        body: { activation_key: activationKey, app_version: appVersion }
      });
  return mapResponse(response, mapCommerceLicenseStatus);
}
