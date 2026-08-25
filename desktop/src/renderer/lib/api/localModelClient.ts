import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type { LocalLLMHealth, LocalModelSetupPlan } from "../../../shared/localModelTypes";
import { safeIpcApiRequest } from "./apiRequestSession";
import { compactLocalModelRequest } from "./mappers";
import type { BackendLocalLlmHealth, BackendLocalModelSetupPlan } from "./localModelBackendTypes";
import { mapLocalLlmHealth, mapLocalModelSetupPlan } from "./localModelMappers";
import { mapResponse } from "./transport";
import type { LocalModelInstallRequest, LocalModelInstallResponse, OllamaActionResponse } from "./transport";

export type LocalModelEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function getLocalLlmHealthEndpoint(
  request: LocalModelEndpointRequest
): Promise<ApiResponse<LocalLLMHealth>> {
  return request<BackendLocalLlmHealth>({
    endpoint: "/api/settings/local-llm/health",
    timeoutMs: 2500
  }).then((response) => mapResponse(response, mapLocalLlmHealth));
}

export function getLocalModelSetupPlanEndpoint(
  request: LocalModelEndpointRequest,
  model?: string
): Promise<ApiResponse<LocalModelSetupPlan>> {
  return request<BackendLocalModelSetupPlan>({
    endpoint: "/api/settings/local-model/setup-plan",
    query: model ? { model } : undefined,
    timeoutMs: 10_000
  }).then((response) => mapResponse(response, mapLocalModelSetupPlan));
}

export function installLocalModelEndpoint(
  request: LocalModelEndpointRequest,
  installRequest: LocalModelInstallRequest = {}
): Promise<ApiResponse<LocalModelInstallResponse>> {
  const body = compactLocalModelRequest(installRequest);
  if (window.lengrvis?.localModel) {
    return safeIpcApiRequest(() =>
      window.lengrvis.localModel.install(body) as Promise<ApiResponse<LocalModelInstallResponse>>
    );
  }
  return request<LocalModelInstallResponse, LocalModelInstallRequest>({
    endpoint: "/api/settings/install-local-model",
    method: "POST",
    body,
    timeoutMs: 120_000
  });
}

export function installOllamaEndpoint(
  request: LocalModelEndpointRequest
): Promise<ApiResponse<OllamaActionResponse>> {
  if (window.lengrvis?.ollama) {
    return safeIpcApiRequest(() =>
      window.lengrvis.ollama.install() as Promise<ApiResponse<OllamaActionResponse>>
    );
  }
  return request<OllamaActionResponse>({
    endpoint: "/api/settings/ollama/install",
    method: "POST",
    timeoutMs: 120_000
  });
}

export function startOllamaEndpoint(
  request: LocalModelEndpointRequest
): Promise<ApiResponse<OllamaActionResponse>> {
  if (window.lengrvis?.ollama) {
    return safeIpcApiRequest(() =>
      window.lengrvis.ollama.start() as Promise<ApiResponse<OllamaActionResponse>>
    );
  }
  return request<OllamaActionResponse>({
    endpoint: "/api/settings/ollama/start",
    method: "POST",
    timeoutMs: 30_000
  });
}

export function pullOllamaEndpoint(
  request: LocalModelEndpointRequest,
  model?: string
): Promise<ApiResponse<OllamaActionResponse>> {
  const body = compactLocalModelRequest({ model });
  if (window.lengrvis?.ollama) {
    return safeIpcApiRequest(() =>
      window.lengrvis.ollama.pull(body) as Promise<ApiResponse<OllamaActionResponse>>
    );
  }
  return request<OllamaActionResponse, LocalModelInstallRequest>({
    endpoint: "/api/settings/ollama/pull",
    method: "POST",
    body,
    timeoutMs: 120_000
  });
}
