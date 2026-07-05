import type { BackendPermissionPolicy, BackendPermissionRule } from "./backendTypes";
import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type { AppSettings } from "../../../shared/settingsTypes";
import type { BackendSettings, SensitiveChangeConfirmation } from "./settingsBackendTypes";
import { mapSettings, mergeDesktopOnlySettings, settingsPatchFor } from "./settingsMappers";
import { mapResponse } from "./transport";

export type SettingsEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export interface SettingsEndpointState {
  getLastLoadedSettings(): AppSettings | null;
  setLastLoadedSettings(settings: AppSettings): void;
}

export function getSettingsEndpoint(
  request: SettingsEndpointRequest,
  state: SettingsEndpointState
): Promise<ApiResponse<AppSettings>> {
  return request<BackendSettings>({ endpoint: "/api/settings" }).then((response) => {
    const mapped = mapResponse(response, mapSettings);
    if (mapped.ok && mapped.data) {
      mapped.data = mergeDesktopOnlySettings(mapped.data, state.getLastLoadedSettings());
      state.setLastLoadedSettings(mapped.data);
    }
    return mapped;
  });
}

export async function saveSettingsEndpoint(
  request: SettingsEndpointRequest,
  state: SettingsEndpointState,
  settings: AppSettings
): Promise<ApiResponse<AppSettings>> {
  const body = settingsPatchFor(settings, state.getLastLoadedSettings());
  const confirmation = window.lengrvis?.settings
    ? await (window.lengrvis.settings.confirmSensitiveChange(body as Record<string, unknown>) as Promise<
        ApiResponse<SensitiveChangeConfirmation>
      >)
    : await request<SensitiveChangeConfirmation, Partial<BackendSettings>>({
        endpoint: "/api/settings/confirm-sensitive-change",
        method: "POST",
        body
      });
  if (confirmation.ok && confirmation.data?.required && confirmation.data.nonce) {
    body.confirmation_nonce = confirmation.data.nonce;
  }
  const responsePromise = window.lengrvis?.settings
    ? window.lengrvis.settings.save(body as Record<string, unknown>) as Promise<ApiResponse<BackendSettings>>
    : request<BackendSettings, Partial<BackendSettings>>({
        endpoint: "/api/settings",
        method: "POST",
        body
      });
  return responsePromise.then((response) => {
    const mapped = mapResponse(response, mapSettings);
    if (mapped.ok && mapped.data) {
      mapped.data = mergeDesktopOnlySettings(mapped.data, settings);
      state.setLastLoadedSettings(mapped.data);
    }
    return mapped;
  });
}

export async function confirmPermissionRuleChangeEndpoint(
  request: SettingsEndpointRequest,
  rule: BackendPermissionRule
): Promise<ApiResponse<SensitiveChangeConfirmation>> {
  if (window.lengrvis?.permissionPolicy) {
    return window.lengrvis.permissionPolicy.confirmRelaxation({ action: "upsert_rule", rule }) as Promise<
      ApiResponse<SensitiveChangeConfirmation>
    >;
  }
  return request<SensitiveChangeConfirmation, { action: string; rule: BackendPermissionRule }>({
    endpoint: "/api/settings/permission-policy/confirm-relaxation",
    method: "POST",
    body: { action: "upsert_rule", rule }
  });
}

export async function confirmPermissionRuleDeleteEndpoint(
  request: SettingsEndpointRequest,
  ruleId: string
): Promise<ApiResponse<SensitiveChangeConfirmation>> {
  if (window.lengrvis?.permissionPolicy) {
    return window.lengrvis.permissionPolicy.confirmRelaxation({ action: "delete_rule", ruleId }) as Promise<
      ApiResponse<SensitiveChangeConfirmation>
    >;
  }
  return request<SensitiveChangeConfirmation, { action: string; rule_id: string }>({
    endpoint: "/api/settings/permission-policy/confirm-relaxation",
    method: "POST",
    body: { action: "delete_rule", rule_id: ruleId }
  });
}

export function upsertPermissionRuleEndpoint(
  request: SettingsEndpointRequest,
  rule: BackendPermissionRule,
  confirmationNonce?: string
): Promise<ApiResponse<BackendPermissionPolicy>> {
  if (window.lengrvis?.permissionPolicy) {
    return window.lengrvis.permissionPolicy.upsertRule({ rule, confirmationNonce }) as Promise<ApiResponse<BackendPermissionPolicy>>;
  }
  return request<BackendPermissionPolicy, BackendPermissionRule>({
    endpoint: "/api/settings/permission-policy/rules",
    method: "POST",
    query: confirmationNonce ? { confirmation_nonce: confirmationNonce } : undefined,
    body: rule
  });
}

export function deletePermissionRuleEndpoint(
  request: SettingsEndpointRequest,
  ruleId: string,
  confirmationNonce?: string
): Promise<ApiResponse<{ ok: boolean; policy: BackendPermissionPolicy }>> {
  if (window.lengrvis?.permissionPolicy) {
    return window.lengrvis.permissionPolicy.deleteRule({ ruleId, confirmationNonce }) as Promise<
      ApiResponse<{ ok: boolean; policy: BackendPermissionPolicy }>
    >;
  }
  return request<{ ok: boolean; policy: BackendPermissionPolicy }>({
    endpoint: `/api/settings/permission-policy/rules/${encodeURIComponent(ruleId)}`,
    method: "DELETE",
    query: confirmationNonce ? { confirmation_nonce: confirmationNonce } : undefined
  });
}
