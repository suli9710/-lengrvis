import { useCallback, useEffect, useMemo, useRef } from "react";

import type { ChatMessage } from "../../shared/catalogTypes";
import type { AgentConversation, ApprovalRequest, Plan, SafetyReview, TaskEvent } from "../../shared/executionTypes";
import {
  latestStreamableTaskId as latestStreamableTaskIdFromEvents,
  mergeRunStreamEventIntoConversations,
  mergeStreamedAgentMessage,
  preserveStreamedRunConversations as preserveStreamedRunConversationsFromEvents
} from "../events";
import type { LengrvisApiClient, RealtimeConnectionStatus } from "../lib/apiClient";
import { zhBackendText } from "../lib/zh";
import type { ViewKey } from "../store";
import {
  appendUniqueMessage,
  chatMessageFromLegacyTaskTerminal,
  chatMessageFromRunTerminalEvent,
  chatMessageFromTaskAgentMessage,
  isActiveTask,
  latestLegacyTaskIdFromEvents,
  mergeTaskSnapshots,
  wasUpdatedRecently
} from "../appViewModel";

interface UseTaskRealtimeSyncOptions {
  api: LengrvisApiClient;
  tasks: TaskEvent[];
  hasLoadedBackendTasks: boolean;
  realtimeStatus: RealtimeConnectionStatus | null;
  setRealtimeStatus: (status: RealtimeConnectionStatus | null) => void;
  setMessages: (messages: ChatMessage[] | ((current: ChatMessage[]) => ChatMessage[])) => void;
  setTasks: (tasks: TaskEvent[] | ((current: TaskEvent[]) => TaskEvent[])) => void;
  setPlan: (plan: Plan) => void;
  setAgentConversations: (
    conversations: AgentConversation[] | ((current: AgentConversation[]) => AgentConversation[])
  ) => void;
  setSafetyReview: (review: SafetyReview) => void;
  setApprovalRequests: (approvalRequests: ApprovalRequest[] | ((current: ApprovalRequest[]) => ApprovalRequest[])) => void;
  setFocusedTaskId: (taskId: string | null) => void;
  setActiveView: (view: ViewKey) => void;
  onRealtimeStatus: (status: RealtimeConnectionStatus) => void;
  onRealtimeBadMessage: (status: RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string }) => void;
}

export function useTaskRealtimeSync({
  api,
  tasks,
  hasLoadedBackendTasks,
  realtimeStatus,
  setRealtimeStatus,
  setMessages,
  setTasks,
  setPlan,
  setAgentConversations,
  setSafetyReview,
  setApprovalRequests,
  setFocusedTaskId,
  setActiveView,
  onRealtimeStatus,
  onRealtimeBadMessage
}: UseTaskRealtimeSyncOptions) {
  const chatStartedTaskIds = useRef(new Set<string>());
  const announcedTerminalTaskIds = useRef(new Set<string>());
  const refreshTaskSnapshotTimer = useRef<number | null>(null);
  const taskSnapshotAbortRef = useRef<AbortController | null>(null);
  const hasRunningTaskRef = useRef(false);
  const realtimeStatusRef = useRef(realtimeStatus);

  const latestTaskId = useMemo(
    () => hasLoadedBackendTasks ? latestStreamableTaskIdFromEvents(tasks) : null,
    [hasLoadedBackendTasks, tasks]
  );
  const latestLegacyTaskId = useMemo(
    () => latestLegacyTaskIdFromEvents(tasks, chatStartedTaskIds.current, announcedTerminalTaskIds.current),
    [tasks]
  );

  useEffect(() => {
    realtimeStatusRef.current = realtimeStatus;
  }, [realtimeStatus]);

  useEffect(() => {
    if (!latestTaskId && !latestLegacyTaskId) {
      setRealtimeStatus(null);
    }
  }, [latestLegacyTaskId, latestTaskId, setRealtimeStatus]);

  const refreshTaskSnapshot = useCallback(async () => {
    taskSnapshotAbortRef.current?.abort();
    const controller = new AbortController();
    taskSnapshotAbortRef.current = controller;
    const { signal } = controller;
    let batchStarted = false;
    try {
      await api.beginBatch("task-snapshot");
      batchStarted = true;
      const [runsResult, legacyTasksResult, planResult, agentsResult, safetyResult, approvalsResult] = await Promise.allSettled([
        api.listRuns(),
        api.listTaskTimeline(),
        api.getCurrentPlan(),
        api.listAgentConversations(),
        api.getSafetyReview(),
        api.listPendingApprovals()
      ]);
      if (signal.aborted) return;
      const runTasks = runsResult.status === "fulfilled" && runsResult.value.ok ? runsResult.value.data : undefined;
      const legacyTasks =
        legacyTasksResult.status === "fulfilled" && legacyTasksResult.value.ok ? legacyTasksResult.value.data : undefined;
      if (runTasks || legacyTasks) {
        setTasks(mergeTaskSnapshots(runTasks ?? [], legacyTasks ?? []));
      }
      if (planResult.status === "fulfilled" && planResult.value.ok && planResult.value.data) setPlan(planResult.value.data);
      if (agentsResult.status === "fulfilled" && agentsResult.value.ok && agentsResult.value.data) {
        setAgentConversations((current) => preserveStreamedRunConversationsFromEvents(current, agentsResult.value.data ?? []));
      }
      if (safetyResult.status === "fulfilled" && safetyResult.value.ok && safetyResult.value.data) setSafetyReview(safetyResult.value.data);
      if (approvalsResult.status === "fulfilled" && approvalsResult.value.ok && approvalsResult.value.data) {
        setApprovalRequests(approvalsResult.value.data);
      }
    } finally {
      if (batchStarted) api.endBatch("task-snapshot");
    }
  }, [api]);

  const scheduleTaskSnapshotRefresh = useCallback(() => {
    if (refreshTaskSnapshotTimer.current !== null) return;
    refreshTaskSnapshotTimer.current = window.setTimeout(() => {
      refreshTaskSnapshotTimer.current = null;
      void refreshTaskSnapshot();
    }, 1200);
  }, [refreshTaskSnapshot]);

  useEffect(() => () => {
    if (refreshTaskSnapshotTimer.current !== null) {
      window.clearTimeout(refreshTaskSnapshotTimer.current);
      refreshTaskSnapshotTimer.current = null;
    }
    taskSnapshotAbortRef.current?.abort();
    api.abortInflight("task-snapshot");
  }, [api]);

  useEffect(() => {
    hasRunningTaskRef.current = tasks.some(
      (task) => isActiveTask(task) && (Boolean(task.runId) || wasUpdatedRecently(task))
    );
  }, [tasks]);

  useEffect(() => {
    if (!hasLoadedBackendTasks) return;
    const wsHealthy = realtimeStatusRef.current?.state === "open";
    const pollIntervalMs = wsHealthy ? 30_000 : 10_000;
    const intervalId = window.setInterval(() => {
      if (!hasRunningTaskRef.current) return;
      void refreshTaskSnapshot();
    }, pollIntervalMs);
    return () => window.clearInterval(intervalId);
  }, [hasLoadedBackendTasks, realtimeStatus?.state, refreshTaskSnapshot]);

  useEffect(() => {
    if (!latestTaskId) return;

    const unsubscribe = api.subscribeRunEvents(latestTaskId, {
      onMessage: (event) => {
        if (event.type === "run_event") {
          setAgentConversations((current) => mergeRunStreamEventIntoConversations(current, latestTaskId, event));
          const terminalMessage = chatMessageFromRunTerminalEvent(event);
          if (terminalMessage) {
            setMessages((current) => appendUniqueMessage(current, terminalMessage));
          }
          scheduleTaskSnapshotRefresh();
        }
      },
      onStatus: onRealtimeStatus,
      onBadMessage: onRealtimeBadMessage
    });

    return () => {
      unsubscribe();
    };
  }, [api, latestTaskId, onRealtimeBadMessage, onRealtimeStatus, scheduleTaskSnapshotRefresh, setAgentConversations, setMessages]);

  useEffect(() => {
    if (!latestLegacyTaskId) return;

    const unsubscribe = api.subscribeTaskMessages(latestLegacyTaskId, {
      onMessage: (event) => {
        if (event.type !== "agent_message" || !event.message) return;
        const message = {
          ...event.message,
          content: zhBackendText(event.message.content)
        };
        setAgentConversations((current) => mergeStreamedAgentMessage(current, latestLegacyTaskId, message));
        const chatMessage = chatMessageFromTaskAgentMessage(latestLegacyTaskId, message);
        if (chatMessage) {
          setMessages((current) => appendUniqueMessage(current, chatMessage));
        }
        scheduleTaskSnapshotRefresh();
      },
      onStatus: onRealtimeStatus,
      onBadMessage: onRealtimeBadMessage
    });

    return () => {
      unsubscribe();
    };
  }, [api, latestLegacyTaskId, onRealtimeBadMessage, onRealtimeStatus, scheduleTaskSnapshotRefresh, setAgentConversations, setMessages]);

  useEffect(() => {
    for (const task of tasks) {
      if (task.runId || !chatStartedTaskIds.current.has(task.id)) continue;
      if (task.state !== "completed" && task.state !== "failed") continue;
      if (announcedTerminalTaskIds.current.has(task.id)) continue;
      const message = chatMessageFromLegacyTaskTerminal(task);
      if (!message) continue;
      announcedTerminalTaskIds.current.add(task.id);
      setMessages((current) => appendUniqueMessage(current, message));
    }
  }, [tasks, setMessages]);

  useEffect(() => {
    const unsubscribe = window.lengrvis?.notifications.onOpenTask((taskId) => {
      setFocusedTaskId(taskId);
      setActiveView("agents");
      void refreshTaskSnapshot();
    });

    return () => {
      unsubscribe?.();
    };
  }, [refreshTaskSnapshot, setActiveView, setFocusedTaskId]);

  return { chatStartedTaskIds, refreshTaskSnapshot };
}
