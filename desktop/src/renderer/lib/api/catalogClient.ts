import type {
  ChatMessage,
  ChatRequest,
  ChatResponse,
  IntentSuggestion,
  PerceptionSuggestionLaunchRequest,
  PerceptionSuggestionLaunchResponse,
  SkillImportResult,
  SkillsCatalog
} from "../../../shared/catalogTypes";
import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import { zhBackendTaskStatus, zhBackendText } from "../zh";
import type {
  BackendChatMessage,
  BackendChatRequest,
  BackendChatResponse,
  BackendIntentSuggestion,
  BackendSkillImportResult,
  BackendSkillRefresh,
  BackendSkillsCatalog
} from "./catalogBackendTypes";
import type {
  BackendSuggestionLaunchRequest,
  BackendSuggestionLaunchResponse
} from "./executionBackendTypes";
import {
  mapChatMessage,
  mapIntentSuggestion,
  mapSkillImportResult,
  mapSkillsCatalog,
  mapSuggestionLaunchResponse,
  mapTaskState
} from "./mappers";
import { ipcRequestFailedResponse } from "./apiRequestSession";
import { mapResponse } from "./transport";

export type CatalogEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function listChatMessagesEndpoint(
  request: CatalogEndpointRequest
): Promise<ApiResponse<ChatMessage[]>> {
  return request<BackendChatMessage[]>({ endpoint: "/api/chat/messages" }).then((response) =>
    mapResponse(response, (messages) => messages.map(mapChatMessage))
  );
}

export function sendChatEndpoint(
  request: CatalogEndpointRequest,
  body: ChatRequest
): Promise<ApiResponse<ChatResponse>> {
  return request<BackendChatResponse, BackendChatRequest>({
    endpoint: "/api/chat",
    method: "POST",
    body: {
      message: body.content,
      mode: body.mode ?? "efficiency"
    }
  }).then((response) =>
    mapResponse(response, (data) => ({
      message: {
        id: `${data.task_id ?? crypto.randomUUID()}-supervisor`,
        role: "assistant" as const,
        author: data.delegated ? "主管 Agent" : "主管 Agent",
        content: zhBackendText(data.message),
        createdAt: new Date().toISOString(),
        status: "sent" as const
      },
      taskUpdates: data.delegated && data.task_id && data.status
        ? [
            {
              id: data.task_id,
              title: "主管已分配任务",
              description: `状态：${zhBackendTaskStatus(data.status)}`,
              state: mapTaskState(data.status),
              agent: data.agent ?? "主管 Agent",
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString()
            }
          ]
        : []
    }))
  );
}

export function listIntentSuggestionsEndpoint(
  request: CatalogEndpointRequest
): Promise<ApiResponse<IntentSuggestion[]>> {
  return request<BackendIntentSuggestion[]>({
    endpoint: "/api/chat/proactive-suggestions",
    timeoutMs: 2500
  }).then((response) => mapResponse(response, (suggestions) => suggestions.map(mapIntentSuggestion)));
}

export async function launchPerceptionSuggestionEndpoint(
  request: CatalogEndpointRequest,
  body: PerceptionSuggestionLaunchRequest
): Promise<ApiResponse<PerceptionSuggestionLaunchResponse>> {
  const requestBody: BackendSuggestionLaunchRequest = {
    suggestion_id: body.suggestionId,
    prompt: body.prompt,
    mode: body.mode ?? "efficiency"
  };
  let response: ApiResponse<BackendSuggestionLaunchResponse>;
  if (window.lengrvis?.perception) {
    try {
      response = await window.lengrvis.perception.launchSuggestion({
        suggestionId: body.suggestionId,
        mode: body.mode ?? "efficiency"
      }) as ApiResponse<BackendSuggestionLaunchResponse>;
    } catch {
      return ipcRequestFailedResponse<PerceptionSuggestionLaunchResponse>();
    }
  } else {
    response = await request<BackendSuggestionLaunchResponse, BackendSuggestionLaunchRequest>({
        endpoint: `/api/perception/suggestions/${encodeURIComponent(body.suggestionId)}/launch`,
        method: "POST",
        body: requestBody,
        timeoutMs: 10_000
      });
  }

  return mapResponse(response, (data) => mapSuggestionLaunchResponse(data, body.prompt ?? body.suggestionId));
}

export function listSkillsEndpoint(
  request: CatalogEndpointRequest
): Promise<ApiResponse<SkillsCatalog>> {
  return request<BackendSkillsCatalog>({ endpoint: "/api/skills" }).then((response) =>
    mapResponse(response, mapSkillsCatalog)
  );
}

export function importSkillEndpoint(
  request: CatalogEndpointRequest,
  path: string
): Promise<ApiResponse<SkillImportResult>> {
  if (window.lengrvis?.skills) {
    return window.lengrvis.skills.importPackage(path)
      .then((response) => mapResponse(response as ApiResponse<BackendSkillImportResult>, mapSkillImportResult))
      .catch(() => ipcRequestFailedResponse<SkillImportResult>());
  }
  return request<BackendSkillImportResult, { path: string }>({
    endpoint: "/api/skills/import",
    method: "POST",
    body: { path },
    timeoutMs: 30_000
  }).then((response) => mapResponse(response, mapSkillImportResult));
}

export function refreshSkillsEndpoint(
  request: CatalogEndpointRequest
): Promise<ApiResponse<{ ok: boolean; toolCount: number; skillCount: number }>> {
  const mapRefresh = (data: BackendSkillRefresh) => ({
    ok: Boolean(data.ok),
    toolCount: Number(data.tool_count ?? 0),
    skillCount: Number(data.skill_count ?? 0)
  });

  if (window.lengrvis?.skills) {
    return window.lengrvis.skills.refresh()
      .then((response) => mapResponse(response as ApiResponse<BackendSkillRefresh>, mapRefresh))
      .catch(() => ipcRequestFailedResponse<{ ok: boolean; toolCount: number; skillCount: number }>());
  }
  return request<BackendSkillRefresh>({ endpoint: "/api/skills/refresh", method: "POST" }).then((response) =>
    mapResponse(response, mapRefresh)
  );
}
