import type { ChatRequest, ChatResponse } from "../../../shared/catalogTypes";
import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  AgentConversation,
  ApprovalDecision,
  ApprovalRequest,
  CommandExecutionResult,
  CommandInfo,
  Plan,
  SafetyReview,
  TaskArtifactsSummary,
  TaskEvent,
  TaskExplain
} from "../../../shared/executionTypes";
import { zhBackendTaskStatus, zhBackendText, zhRiskLevel, zhSafetyVerdict, zhToolName } from "../zh";
import type {
  BackendAgentMessage,
  BackendApproval,
  BackendCommandExecutionResult,
  BackendCommandsResponse,
  BackendPlan,
  BackendRunCreateRequest,
  BackendRunCreateResponse,
  BackendRunState,
  BackendRunStreamEvent,
  BackendRunTimeline,
  BackendSafetyReview,
  BackendTask,
  BackendTaskArtifacts,
  BackendTaskExplain,
  BackendTaskStreamEvent,
  BackendTimeline
} from "./executionBackendTypes";
import {
  agentNameFor,
  cleanupPlanFromTimeline,
  emptyPlan,
  emptySafetyReview,
  hasRunTimelineEvents,
  latestRunState,
  mapAgentKind,
  mapApproval,
  mapBoundaryEvents,
  mapCommandExecutionResult,
  mapCommandInfo,
  mapRiskSeverity,
  mapRunConversation,
  mapRunPlan,
  mapRunTaskEvent,
  mapTaskEvent,
  mapTaskExplain,
  mapTaskRecordings,
  mapTaskState,
  metadataPayloadFor,
  runEngineAgentName
} from "./mappers";
import { subscribeJsonRealtime, type JsonRealtimeHandlers } from "./realtimeTransport";
import { mapResponse } from "./transport";

export type ExecutionEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function startRunEndpoint(
  request: ExecutionEndpointRequest,
  body: ChatRequest
): Promise<ApiResponse<ChatResponse>> {
  const requestBody: BackendRunCreateRequest = {
    message: body.content,
    mode: body.mode ?? "efficiency",
    engine: "auto"
  };
  const responsePromise = window.lengrvis?.runs
    ? window.lengrvis.runs.start(requestBody) as Promise<ApiResponse<BackendRunCreateResponse>>
    : request<BackendRunCreateResponse, BackendRunCreateRequest>({
        endpoint: "/api/runs",
        method: "POST",
        body: requestBody
      });
  return responsePromise.then((response) =>
    mapResponse(response, (data) => ({
      runId: data.run_id,
      engine: data.engine,
      message: {
        id: `${data.run_id}-run-started`,
        role: "assistant" as const,
        author: "Lengrvis",
        content: `已开始处理任务，当前状态：${zhBackendTaskStatus(data.phase)}。`,
        createdAt: new Date().toISOString(),
        status: "sent" as const
      },
      taskUpdates: [
        {
          id: data.run_id,
          runId: data.run_id,
          title: body.content,
          description: `状态：${zhBackendTaskStatus(data.phase)}`,
          state: mapTaskState(data.phase),
          agent: runEngineAgentName(data.engine),
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        }
      ]
    }))
  );
}

export function listRunsEndpoint(
  request: ExecutionEndpointRequest
): Promise<ApiResponse<TaskEvent[]>> {
  return request<BackendRunState[]>({ endpoint: "/api/runs" }).then((response) =>
    mapResponse(response, (runs) => runs.map(mapRunTaskEvent))
  );
}

export function getRunTimelineEndpoint(
  request: ExecutionEndpointRequest,
  runId: string
): Promise<ApiResponse<BackendRunTimeline>> {
  return request<BackendRunTimeline>({ endpoint: `/api/runs/${runId}/timeline`, timeoutMs: 10_000 });
}

export async function listTaskTimelineEndpoint(
  request: ExecutionEndpointRequest
): Promise<ApiResponse<TaskEvent[]>> {
  const response = await request<BackendTask[]>({ endpoint: "/api/tasks" });
  if (!response.ok || !response.data) {
    return mapResponse(response, () => []);
  }
  return {
    ok: true,
    status: response.status,
    data: response.data.map(mapTaskEvent),
    receivedAt: response.receivedAt
  };
}

export async function getTaskTimelineEventEndpoint(
  request: ExecutionEndpointRequest,
  taskId: string
): Promise<ApiResponse<TaskEvent>> {
  const taskResponse = await request<BackendTask>({ endpoint: `/api/tasks/${taskId}` });
  if (!taskResponse.ok || !taskResponse.data) {
    return mapResponse(taskResponse, () => {
      throw new Error("Task not found");
    });
  }
  const event = await mapTaskEventWithRecordings(request, taskResponse.data);
  return {
    ok: true,
    status: taskResponse.status,
    data: event,
    receivedAt: taskResponse.receivedAt
  };
}

export function listTaskArtifactsEndpoint(
  request: ExecutionEndpointRequest,
  taskId: string
): Promise<ApiResponse<TaskArtifactsSummary>> {
  return request<BackendTaskArtifacts>({
    endpoint: `/api/tasks/${taskId}/artifacts`,
    timeoutMs: 10_000
  }).then((response) =>
    mapResponse(response, (data) => ({
      taskId: data.task_id,
      artifacts: (data.artifacts ?? []).map((item) => ({
        path: item.path,
        kind: item.kind,
        toolName: item.tool_name,
        stepId: item.step_id,
        createdAt: item.created_at,
        exists: Boolean(item.exists),
        isDir: Boolean(item.is_dir),
        sizeBytes: Number(item.size_bytes ?? 0)
      })),
      counts: {
        total: Number(data.counts?.total ?? 0),
        existing: Number(data.counts?.existing ?? 0),
        missing: Number(data.counts?.missing ?? 0),
        changed: Number(data.counts?.changed ?? 0),
        generated: Number(data.counts?.generated ?? 0)
      }
    }))
  );
}

export async function getCurrentPlanEndpoint(
  request: ExecutionEndpointRequest
): Promise<ApiResponse<Plan>> {
  const runsResponse = await request<BackendRunState[]>({ endpoint: "/api/runs" });
  const latestRun = runsResponse.ok && runsResponse.data?.length ? latestRunState(runsResponse.data) : null;
  if (latestRun) {
    const timeline = await getRunTimelineEndpoint(request, latestRun.run_id);
    if (timeline.ok && timeline.data && hasRunTimelineEvents(timeline.data)) {
      return mapResponse(timeline, (data) => mapRunPlan(latestRun, data));
    }
  }

  return request<BackendTask[]>({ endpoint: "/api/tasks" }).then(async (tasksResponse) => {
    if (!tasksResponse.ok || !tasksResponse.data?.[0]) {
      return mapResponse(tasksResponse, () => emptyPlan());
    }

    const task = tasksResponse.data[0];
    const timeline = await request<BackendTimeline>({ endpoint: `/api/tasks/${task.id}/timeline` });
    return mapResponse(timeline, (data) => {
      const plannerMessage = [...data.messages].reverse().find((message) => agentNameFor(message) === "PlannerAgent");
      const rawPlan = metadataPayloadFor<BackendPlan>(plannerMessage);
      if (!rawPlan?.steps?.length) {
        return {
          ...emptyPlan(),
          id: task.id,
          title: task.user_goal,
          objective: task.final_summary || task.user_goal,
          updatedAt: task.updated_at
        };
      }
      return {
        id: rawPlan.id,
        title: rawPlan.goal,
        objective: rawPlan.assumptions?.join(" ") || task.user_goal,
        updatedAt: task.updated_at,
        steps: rawPlan.steps.map((step) => ({
          id: step.id,
          title: zhToolName(step.tool_name),
          detail: zhBackendText(step.description),
          state: step.status === "succeeded" ? "done" : step.status === "waiting_user_approval" ? "blocked" : "pending",
          owner: step.agent_name,
          toolName: step.tool_name,
          riskLevel: step.risk_level,
          effects: step.tool_effects ?? [],
          resourceKinds: step.resource_kinds ?? [],
          trustTier: step.trust_tier,
          approvalState: step.requires_approval ? "required" : "not_required",
          deferredTool: Boolean(step.deferred_tool)
        }))
      };
    });
  });
}

export async function listAgentConversationsEndpoint(
  request: ExecutionEndpointRequest
): Promise<ApiResponse<AgentConversation[]>> {
  const runsResponse = await request<BackendRunState[]>({ endpoint: "/api/runs" });
  const latestRun = runsResponse.ok && runsResponse.data?.length ? latestRunState(runsResponse.data) : null;
  if (latestRun) {
    const timeline = await getRunTimelineEndpoint(request, latestRun.run_id);
    if (timeline.ok && timeline.data && hasRunTimelineEvents(timeline.data)) {
      return mapResponse(timeline, (data) => [mapRunConversation(latestRun, data.events)]);
    }
  }

  return request<BackendTask[]>({ endpoint: "/api/tasks" }).then(async (tasksResponse) => {
    if (!tasksResponse.ok || !tasksResponse.data?.[0]) {
      return mapResponse(tasksResponse, () => []);
    }
    const task = tasksResponse.data[0];
    const response = await request<BackendAgentMessage[]>({
      endpoint: `/api/tasks/${task.id}/agent-messages`
    });
    return mapResponse(response, (messages) => [
      {
        id: `${task.id}-agents`,
        title: task.user_goal,
        status: task.status === "completed" ? "done" : task.status === "waiting_user_approval" ? "waiting" : "running",
        messages: messages.map((message) => ({
          id: message.id,
          role: message.role ?? "assistant",
          name: agentNameFor(message),
          agent: agentNameFor(message),
          content: zhBackendText(message.content),
          createdAt: message.created_at,
          toolCalls: message.tool_calls,
          toolCallId: message.tool_call_id,
          metadata: message.metadata,
          kind: mapAgentKind(message.metadata?.message_type ?? message.message_type)
        }))
      }
    ]);
  });
}

export function subscribeTaskMessagesEndpoint(
  taskId: string,
  handlers: JsonRealtimeHandlers<BackendTaskStreamEvent>
): () => void {
  if (!taskId) {
    return () => undefined;
  }

  return subscribeJsonRealtime<BackendTaskStreamEvent>({ endpoint: `/ws/tasks/${encodeURIComponent(taskId)}` }, handlers);
}

export function getSafetyReviewEndpoint(
  request: ExecutionEndpointRequest
): Promise<ApiResponse<SafetyReview>> {
  return request<BackendTask[]>({ endpoint: "/api/tasks" }).then(async (tasksResponse) => {
    if (!tasksResponse.ok || !tasksResponse.data?.[0]) {
      return mapResponse(tasksResponse, () => emptySafetyReview());
    }
    const task = tasksResponse.data[0];
    const response = await request<BackendSafetyReview[]>({
      endpoint: `/api/tasks/${task.id}/safety-reviews`
    });
    return mapResponse(response, (reviews) => ({
      id: `${task.id}-safety`,
      status: reviews.some((review) => review.verdict === "deny")
        ? "blocked"
        : reviews.some((review) => review.verdict === "needs_user_approval")
          ? "needs_review"
          : "clear",
      updatedAt: reviews[0]?.created_at ?? task.updated_at,
      boundaryEvents: mapBoundaryEvents(task.boundary_events),
      findings: reviews.map((review) => ({
        id: review.id,
        severity: mapRiskSeverity(review.risk_level),
        title: `${review.target_type}：${zhSafetyVerdict(review.verdict)} · ${zhRiskLevel(review.risk_level)}`,
        detail: review.reasons.map(zhBackendText).join(" ") || zhBackendText(review.safe_alternative) || "无安全发现。",
        status: review.verdict === "allow" ? "accepted" : "open"
      }))
    }));
  });
}

export function listPendingApprovalsEndpoint(
  request: ExecutionEndpointRequest
): Promise<ApiResponse<ApprovalRequest[]>> {
  return request<BackendApproval[]>({ endpoint: "/api/approvals/pending" }).then((response) =>
    mapResponse(response, (approvals) => approvals.map(mapApproval))
  );
}

export function submitApprovalDecisionEndpoint(
  request: ExecutionEndpointRequest,
  decision: ApprovalDecision
): Promise<ApiResponse<ApprovalRequest>> {
  const action = decision.decision === "approved" ? "approve" : "reject";
  if (window.lengrvis?.approvals) {
    const bridgeRequest = action === "approve"
      ? window.lengrvis.approvals.approve(decision.approvalId)
      : window.lengrvis.approvals.reject(decision.approvalId);
    return bridgeRequest.then((response) => mapResponse(response, mapApproval));
  }
  return request<BackendApproval>({
    endpoint: `/api/approvals/${decision.approvalId}/${action}`,
    method: "POST"
  }).then((response) => mapResponse(response, mapApproval));
}

export function listCommandsEndpoint(
  request: ExecutionEndpointRequest
): Promise<ApiResponse<CommandInfo[]>> {
  return request<BackendCommandsResponse>({ endpoint: "/api/commands" }).then((response) =>
    mapResponse(response, (data) => (data.commands ?? []).map(mapCommandInfo))
  );
}

export function executeCommandEndpoint(
  request: ExecutionEndpointRequest,
  name: string,
  args: Record<string, unknown> = {}
): Promise<ApiResponse<CommandExecutionResult>> {
  if (window.lengrvis?.commands) {
    return window.lengrvis.commands.execute({ name, args }).then((response) =>
      mapResponse(response as ApiResponse<BackendCommandExecutionResult>, mapCommandExecutionResult)
    );
  }
  return request<BackendCommandExecutionResult, { name: string; args: Record<string, unknown> }>({
    endpoint: "/api/commands/execute",
    method: "POST",
    body: { name, args }
  }).then((response) => mapResponse(response, mapCommandExecutionResult));
}

export function previewRollbackEndpoint(
  request: ExecutionEndpointRequest,
  taskId: string
): Promise<ApiResponse<{ task_id: string; steps: unknown[]; count: number }>> {
  return request({ endpoint: `/api/tasks/${taskId}/rollback-preview` });
}

export function executeRollbackEndpoint(
  request: ExecutionEndpointRequest,
  taskId: string
): Promise<ApiResponse<{ executed: unknown[]; count: number }>> {
  const response = window.lengrvis?.tasks
    ? window.lengrvis.tasks.rollback(taskId)
    : request({ endpoint: `/api/tasks/${taskId}/rollback`, method: "POST" });
  return response as Promise<ApiResponse<{ executed: unknown[]; count: number }>>;
}

export function subscribeRunEventsEndpoint(
  runId: string,
  handlers: JsonRealtimeHandlers<BackendRunStreamEvent>
): () => void {
  if (!runId) {
    return () => undefined;
  }

  return subscribeJsonRealtime<BackendRunStreamEvent>({ endpoint: `/ws/runs/${encodeURIComponent(runId)}` }, handlers);
}

export function getTaskExplainEndpoint(
  request: ExecutionEndpointRequest,
  taskId: string
): Promise<ApiResponse<TaskExplain>> {
  return request<BackendTaskExplain>({
    endpoint: `/api/tasks/${taskId}/explain`,
    timeoutMs: 10_000
  }).then((response) => mapResponse(response, mapTaskExplain));
}

async function mapTaskEventWithRecordings(
  request: ExecutionEndpointRequest,
  task: BackendTask
): Promise<TaskEvent> {
  const base = mapTaskEvent(task);
  const timeline = await request<BackendTimeline>({
    endpoint: `/api/tasks/${task.id}/timeline`,
    timeoutMs: 10_000
  });
  if (!timeline.ok || !timeline.data) {
    return base;
  }
  const boundaryEvents = mapBoundaryEvents(timeline.data.boundary_events);
  return {
    ...base,
    recordings: mapTaskRecordings(timeline.data),
    cleanupPlan: base.cleanupPlan ?? cleanupPlanFromTimeline(timeline.data),
    boundaryEvents: boundaryEvents.length ? boundaryEvents : base.boundaryEvents
  };
}
