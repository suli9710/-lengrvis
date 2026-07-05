import type {
  CleanupExecutionResult,
  CleanupExecuteRequest,
  CleanupPlan,
  CleanupPlanRequest,
  CleanupRollbackRequest,
  CleanupScanRequest
} from "../../../shared/cleanupTypes";
import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  BackendCleanupExecuteRequest,
  BackendCleanupExecutionResult,
  BackendCleanupPlan,
  BackendCleanupPlanRequest,
  BackendCleanupRollbackRequest,
  BackendCleanupScanRequest
} from "./cleanupBackendTypes";
import { cleanupScanRequestFor, mapCleanupExecutionResult, mapCleanupPlan } from "./mappers";
import { mapResponse } from "./transport";

export type CleanupEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function scanCleanupEndpoint(
  request: CleanupEndpointRequest,
  body: CleanupScanRequest = {}
): Promise<ApiResponse<CleanupPlan>> {
  return request<BackendCleanupPlan, BackendCleanupScanRequest>({
    endpoint: "/api/files/cleanup/scan",
    method: "POST",
    body: cleanupScanRequestFor(body),
    timeoutMs: 30_000
  }).then((response) => mapResponse(response, mapCleanupPlan));
}

export function planCleanupEndpoint(
  request: CleanupEndpointRequest,
  body: CleanupPlanRequest = {}
): Promise<ApiResponse<CleanupPlan>> {
  return request<BackendCleanupPlan, BackendCleanupPlanRequest>({
    endpoint: "/api/files/cleanup/plan",
    method: "POST",
    body: {
      ...cleanupScanRequestFor(body),
      item_ids: body.itemIds,
      prefer_trash: body.preferTrash
    },
    timeoutMs: 30_000
  }).then((response) => mapResponse(response, mapCleanupPlan));
}

export function executeCleanupEndpoint(
  request: CleanupEndpointRequest,
  body: CleanupExecuteRequest
): Promise<ApiResponse<CleanupExecutionResult>> {
  const selectedItemIds = body.selectedItemIds ?? body.items?.map((item) => item.id);
  const requestBody: BackendCleanupExecuteRequest = {
    roots: body.roots,
    plan_id: body.planId,
    content_hash: body.contentHash,
    selected_item_ids: selectedItemIds,
    dry_run: body.dryRun,
    approved: body.approved,
    approval_id: body.approvalId
  };
  const response = window.lengrvis?.cleanup
    ? window.lengrvis.cleanup.execute(requestBody as Record<string, unknown>)
    : request<BackendCleanupExecutionResult, BackendCleanupExecuteRequest>({
        endpoint: "/api/files/cleanup/execute",
        method: "POST",
        body: requestBody,
        timeoutMs: 60_000
      });
  return response.then((result) =>
    mapResponse(result as ApiResponse<BackendCleanupExecutionResult>, mapCleanupExecutionResult)
  );
}

export function rollbackCleanupEndpoint(
  request: CleanupEndpointRequest,
  body: CleanupRollbackRequest
): Promise<ApiResponse<CleanupExecutionResult>> {
  const requestBody: BackendCleanupRollbackRequest = {
    plan_id: body.planId,
    execution_id: body.executionId
  };
  const response = window.lengrvis?.cleanup
    ? window.lengrvis.cleanup.rollback(requestBody as Record<string, unknown>)
    : request<BackendCleanupExecutionResult, BackendCleanupRollbackRequest>({
        endpoint: "/api/files/cleanup/rollback",
        method: "POST",
        body: requestBody,
        timeoutMs: 60_000
      });
  return response.then((result) =>
    mapResponse(result as ApiResponse<BackendCleanupExecutionResult>, mapCleanupExecutionResult)
  );
}
