import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type { ContextUsage, LLMCostSummary, LLMHealthStatus, LLMProfile } from "../../../shared/llmContextTypes";
import type {
  BackendContextUsage,
  BackendLlmCostSummary,
  BackendLlmHealth,
  BackendLlmProfileResponse
} from "./llmContextBackendTypes";
import { mapContextUsage, mapLlmCostSummary, mapLlmHealth, mapLlmProfile } from "./llmContextMappers";
import { mapResponse } from "./transport";

export type LlmContextEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function getLlmHealthEndpoint(
  request: LlmContextEndpointRequest
): Promise<ApiResponse<LLMHealthStatus>> {
  return request<BackendLlmHealth>({
    endpoint: "/api/settings/llm/health",
    timeoutMs: 2500
  }).then((response) => mapResponse(response, mapLlmHealth));
}

export function getLlmProfileEndpoint(
  request: LlmContextEndpointRequest
): Promise<ApiResponse<LLMProfile>> {
  return request<BackendLlmProfileResponse>({
    endpoint: "/api/settings/llm/profile",
    timeoutMs: 2500
  }).then((response) => mapResponse(response, (data) => mapLlmProfile(data.profile)));
}

export function getLlmCostSummaryEndpoint(
  request: LlmContextEndpointRequest
): Promise<ApiResponse<LLMCostSummary>> {
  return request<BackendLlmCostSummary>({
    endpoint: "/api/settings/llm/cost-summary",
    timeoutMs: 2500
  }).then((response) => mapResponse(response, mapLlmCostSummary));
}

export function getContextUsageEndpoint(
  request: LlmContextEndpointRequest,
  taskId?: string
): Promise<ApiResponse<ContextUsage>> {
  return request<BackendContextUsage>({
    endpoint: "/api/context/usage",
    query: taskId ? { task_id: taskId } : undefined,
    timeoutMs: 2500
  }).then((response) => mapResponse(response, mapContextUsage));
}
