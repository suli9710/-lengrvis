import type { AgentConversation, ChatMessage, TaskEvent } from "../../shared/types";

export type RunUiEventKind =
  | "run_started"
  | "run_updated"
  | "run_finished"
  | "agent_message"
  | "tool_progress"
  | "tool_result"
  | "approval_needed"
  | "error";

export interface BackendRunStreamEventLike {
  type?: string;
  id?: string;
  event?: string;
  name?: string;
  run_id?: string;
  payload?: Record<string, unknown>;
  created_at?: string;
}

export interface RunUiEvent {
  id: string;
  runId: string;
  name: string;
  kind: RunUiEventKind;
  agent: string;
  content: string;
  createdAt: string;
  payload: Record<string, unknown>;
}

export function normalizeRunStreamEvent(runId: string, rawEvent: BackendRunStreamEventLike): RunUiEvent {
  const payload = rawEvent.payload ?? {};
  const name = String(rawEvent.event ?? rawEvent.name ?? payload.event_type ?? "");
  const eventRunId = String(rawEvent.run_id ?? payload.run_id ?? runId);
  const createdAt = String(rawEvent.created_at ?? payload.created_at ?? new Date().toISOString());
  const id = String(rawEvent.id ?? payload.id ?? `${eventRunId}-${name}-${createdAt}`);
  const agent = String(payload.from_agent ?? payload.agent ?? "ExecutionEngine");
  const content = String(payload.content ?? payload.transition_reason ?? payload.message ?? name);

  return {
    id,
    runId: eventRunId,
    name,
    kind: runEventKind(name, payload),
    agent,
    content,
    createdAt,
    payload
  };
}

export function mergeRunUiEventIntoConversations(
  current: AgentConversation[],
  event: RunUiEvent
): AgentConversation[] {
  const conversationId = `${event.runId}-events`;
  const conversationIndex = current.findIndex((conversation) => conversation.id === conversationId);
  const conversation = current[conversationIndex] ?? {
    id: conversationId,
    title: "Run events",
    status: "running" as const,
    messages: []
  };

  if (conversation.messages.some((item) => item.id === event.id)) {
    return current;
  }

  const nextConversation: AgentConversation = {
    ...conversation,
    status: conversationStatusForEvent(event),
    messages: [
      ...conversation.messages,
      {
        id: event.id,
        role: "assistant",
        name: event.agent,
        agent: event.agent,
        content: event.content,
        createdAt: event.createdAt,
        metadata: { ...event.payload, event_type: event.name },
        kind: conversationMessageKindForEvent(event)
      }
    ]
  };

  if (conversationIndex < 0) {
    return [nextConversation, ...current];
  }
  return current.map((item, index) => (index === conversationIndex ? nextConversation : item));
}

export function mergeRunStreamEventIntoConversations(
  current: AgentConversation[],
  runId: string,
  event: BackendRunStreamEventLike
): AgentConversation[] {
  const uiEvent = normalizeRunStreamEvent(runId, event);
  return mergeRunUiEventIntoConversations(current, uiEvent);
}

export function preserveStreamedRunConversations(
  current: AgentConversation[],
  incoming: AgentConversation[]
): AgentConversation[] {
  const byId = new Map(incoming.map((conversation) => [conversation.id, conversation]));
  for (const conversation of current) {
    if (!conversation.id.endsWith("-events") || conversation.messages.length === 0) continue;
    const snapshot = byId.get(conversation.id);
    if (!snapshot) {
      byId.set(conversation.id, conversation);
      continue;
    }
    const messageIds = new Set(snapshot.messages.map((message) => message.id));
    const streamedMessages = conversation.messages.filter((message) => !messageIds.has(message.id));
    if (streamedMessages.length) {
      byId.set(conversation.id, {
        ...snapshot,
        messages: [...snapshot.messages, ...streamedMessages].sort(
          (left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt)
        )
      });
    }
  }
  return Array.from(byId.values());
}

export function latestStreamableTaskId(tasks: TaskEvent[]): string | null {
  const candidates = tasks.filter((task) => task.state === "running" || task.state === "queued" || task.state === "blocked");
  const task = candidates[0] ?? tasks[0];
  return task?.id ?? null;
}

export function mergeStreamedAgentMessage(
  current: AgentConversation[],
  taskId: string,
  message: {
    id: string;
    role?: ChatMessage["role"];
    name?: string;
    content: string;
    created_at: string;
    tool_calls?: AgentConversation["messages"][number]["toolCalls"];
    tool_call_id?: string;
    metadata?: Record<string, unknown>;
    from_agent?: string;
    message_type?: string;
  }
): AgentConversation[] {
  const conversationIndex = current.findIndex((conversation) => conversation.id === `${taskId}-agents`);
  const conversation = current[conversationIndex] ?? {
    id: `${taskId}-agents`,
    title: "实时任务",
    status: "running" as const,
    messages: []
  };
  if (conversation.messages.some((item) => item.id === message.id)) {
    return current;
  }

  const agentName = message.name ?? String(message.metadata?.from_agent ?? message.from_agent ?? "assistant");
  const nextConversation: AgentConversation = {
    ...conversation,
    status: "running",
    messages: [
      ...conversation.messages,
      {
        id: message.id,
        role: message.role ?? "assistant",
        name: agentName,
        agent: agentName,
        content: message.content,
        createdAt: message.created_at,
        toolCalls: message.tool_calls,
        toolCallId: message.tool_call_id,
        metadata: message.metadata,
        kind: streamAgentKind(String(message.metadata?.message_type ?? message.message_type ?? ""))
      }
    ]
  };

  if (conversationIndex < 0) {
    return [nextConversation, ...current];
  }
  return current.map((item, index) => (index === conversationIndex ? nextConversation : item));
}

function runEventKind(name: string, payload: Record<string, unknown>): RunUiEventKind {
  if (name === "run.started" || name === "run.created") return "run_started";
  if (name === "run.completed" || name === "run.finished") return "run_finished";
  if (name === "run.error" || name === "run.failed" || payload.error) return "error";
  if (name === "approval.needed" || name === "run.waiting_approval") return "approval_needed";
  if (name === "tool.progress") return "tool_progress";
  if (name === "tool.result") return "tool_result";
  if (name === "agent.message" || payload.from_agent || payload.content) return "agent_message";
  return "run_updated";
}

function conversationStatusForEvent(event: RunUiEvent): AgentConversation["status"] {
  if (event.kind === "run_finished" || event.name === "run.completed") return "done";
  if (event.kind === "approval_needed") return "waiting";
  return "running";
}

function conversationMessageKindForEvent(event: RunUiEvent): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (event.kind === "tool_result" || event.kind === "run_finished") return "result";
  if (event.kind === "approval_needed") return "handoff";
  if (event.kind === "tool_progress") return "observation";
  return "action";
}

function streamAgentKind(kind: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (kind === "observation") return "observation";
  if (kind === "review" || kind === "critique") return "handoff";
  if (kind === "final") return "result";
  return "action";
}
