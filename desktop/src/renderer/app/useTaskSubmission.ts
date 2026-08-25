import { useCallback, useRef, useState } from "react";

import type { BackendStatus } from "../../shared/desktopBridgeTypes";
import type { ChatMessage, IntentSuggestion } from "../../shared/catalogTypes";
import type { TaskEvent } from "../../shared/executionTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import type { AssistantMode } from "../store";
import {
  isBackendTaskSubmitReady,
  isReadOnlySystemDiagnosticsPrompt,
  mergeTaskSnapshots,
  readableError,
  withTimeout
} from "../appViewModel";

const TASK_SUBMIT_BACKEND_READY_TIMEOUT_MS = 5_000;

type StateSetter<T> = (value: T | ((current: T) => T)) => void;

interface MutableRefValue<T> {
  current: T;
}

interface UseTaskSubmissionOptions {
  api: LengrvisApiClient;
  mode: AssistantMode;
  backendStatusRef: MutableRefValue<BackendStatus>;
  chatStartedTaskIds: MutableRefValue<Set<string>>;
  setMessages: StateSetter<ChatMessage[]>;
  setTasks: StateSetter<TaskEvent[]>;
  setFocusedTaskId: (taskId: string | null) => void;
  setBackendStatus: (status: BackendStatus) => void;
  refreshTaskSnapshot: () => Promise<void>;
}

export interface TaskSubmissionActions {
  draft: string;
  setDraft: (draft: string) => void;
  heroSubmitting: boolean;
  heroSubmitError: string | null;
  sendMessage: (content: string) => Promise<{ ok: boolean; error?: string }>;
  executeSuggestion: (suggestion: IntentSuggestion) => Promise<void>;
  submitHeroPrompt: () => Promise<void>;
  requestCleanupApproval: (scope: string) => Promise<void>;
}

export function createLocalUserMessage(content: string, id: string, createdAt: string): ChatMessage {
  return {
    id,
    role: "user",
    author: "你",
    content,
    createdAt,
    status: "sent"
  };
}

export function createFailedAssistantMessage(content: string, id: string, createdAt: string): ChatMessage {
  return {
    id,
    role: "assistant",
    author: "Lengrvis",
    content,
    createdAt,
    status: "failed"
  };
}

export function backendTaskSubmitUnavailableMessage(status: BackendStatus): string {
  const healthReason = status.health && !status.health.ok ? "健康检查还没通过" : "";
  return status.message
    ? `Lengrvis 服务还没连上：${status.message}${healthReason ? `，${healthReason}` : ""}。输入内容已保留，可以稍后重试。`
    : `Lengrvis 服务还没连上${healthReason ? `：${healthReason}` : ""}。输入内容已保留，可以稍后重试。`;
}

export function useTaskSubmission({
  api,
  mode,
  backendStatusRef,
  chatStartedTaskIds,
  setMessages,
  setTasks,
  setFocusedTaskId,
  setBackendStatus,
  refreshTaskSnapshot
}: UseTaskSubmissionOptions): TaskSubmissionActions {
  const [draft, setDraft] = useState("");
  const [heroSubmitting, setHeroSubmitting] = useState(false);
  const [heroSubmitError, setHeroSubmitError] = useState<string | null>(null);
  const heroSubmitInFlight = useRef(false);

  const appendFailedAssistantMessage = useCallback((content: string) => {
    setMessages((current) => [
      ...current,
      createFailedAssistantMessage(content, `local-${crypto.randomUUID()}`, new Date().toISOString())
    ]);
  }, [setMessages]);

  const markBackendResponsive = useCallback((message = "后端已响应任务请求") => {
    const currentStatus = backendStatusRef.current;
    if (currentStatus.state === "running" && currentStatus.health?.ok) return;
    const nextStatus = {
      ...currentStatus,
      state: "running" as const,
      message,
      health: {
        ...currentStatus.health,
        ok: true
      },
      lastCheckedAt: new Date().toISOString()
    };
    backendStatusRef.current = nextStatus;
    setBackendStatus(nextStatus);
  }, [backendStatusRef, setBackendStatus]);

  const ensureBackendReadyForTaskSubmit = useCallback(async (): Promise<{ ok: boolean; error?: string }> => {
    try {
      const status = await withTimeout(
        api.getBackendStatus(),
        TASK_SUBMIT_BACKEND_READY_TIMEOUT_MS,
        "连接检查超时"
      );
      backendStatusRef.current = status;
      setBackendStatus(status);
      if (status.health?.ok || (status.state === "running" && !status.health)) {
        markBackendResponsive(status.message ?? "后端已连接，可以启动任务");
        return { ok: true };
      }
      const healthProbeStatus = await api.probeBackendHealth(status.baseUrl);
      if (isBackendTaskSubmitReady(healthProbeStatus)) {
        backendStatusRef.current = {
          ...status,
          ...healthProbeStatus,
          message: healthProbeStatus.message ?? status.message ?? "后端已连接，可以启动任务"
        };
        setBackendStatus(backendStatusRef.current);
        markBackendResponsive(backendStatusRef.current.message);
        return { ok: true };
      }
      return { ok: false, error: backendTaskSubmitUnavailableMessage(status) };
    } catch (error) { // broad-exception-boundary
      return {
        ok: false,
        error: `Lengrvis 服务还没连上：${readableError(error, "连接检查失败")}。输入内容已保留，可以稍后重试。`
      };
    }
  }, [api, backendStatusRef, markBackendResponsive, setBackendStatus]);

  const sendMessage = useCallback(async (content: string): Promise<{ ok: boolean; error?: string }> => {
    const readiness = await ensureBackendReadyForTaskSubmit();
    if (!readiness.ok) {
      return readiness;
    }

    const userMessage = createLocalUserMessage(content, `local-${crypto.randomUUID()}`, new Date().toISOString());
    setMessages((current) => [...current, userMessage]);
    try {
      const preferRun = isReadOnlySystemDiagnosticsPrompt(content);
      let result = preferRun
        ? await api.startRun({ content, mode })
        : await api.sendChat({ content, mode });
      if (!result.ok && !preferRun) {
        result = await api.startRun({ content, mode });
      }

      const response = result.data;
      if (result.ok && response) {
        markBackendResponsive();
        setMessages((current) => [...current, response.message]);
        if (response.taskUpdates?.length) {
          response.taskUpdates.forEach((task) => chatStartedTaskIds.current.add(task.id));
          setTasks((current) => mergeTaskSnapshots(response.taskUpdates ?? [], current));
          setFocusedTaskId(response.taskUpdates[0]?.id ?? null);
          void refreshTaskSnapshot().catch(() => undefined);
        }
        return { ok: true };
      }

      const message = result.error?.message ?? "Lengrvis 暂时不可用，请稍后再试。";
      appendFailedAssistantMessage(message);
      return { ok: false, error: message };
    } catch (error) { // broad-exception-boundary
      const message = error instanceof Error ? error.message : "Lengrvis 暂时不可用，请稍后再试。";
      appendFailedAssistantMessage(message);
      return { ok: false, error: message };
    }
  }, [
    api,
    appendFailedAssistantMessage,
    chatStartedTaskIds,
    ensureBackendReadyForTaskSubmit,
    markBackendResponsive,
    mode,
    refreshTaskSnapshot,
    setFocusedTaskId,
    setMessages,
    setTasks
  ]);

  const executeSuggestion = useCallback(async (suggestion: IntentSuggestion) => {
    const readiness = await ensureBackendReadyForTaskSubmit();
    if (!readiness.ok) {
      appendFailedAssistantMessage(readiness.error ?? "建议任务没有启动成功，输入内容未发送，可以稍后重试。");
      return;
    }

    const userMessage = createLocalUserMessage(suggestion.prompt, `local-${crypto.randomUUID()}`, new Date().toISOString());
    setMessages((current) => [...current, userMessage]);

    try {
      const result = await api.launchPerceptionSuggestion({
        suggestionId: suggestion.id,
        prompt: suggestion.prompt,
        mode
      });

      const response = result.data;
      if (result.ok && response) {
        setMessages((current) => [...current, response.message]);
        if (response.taskUpdates?.length) {
          setTasks((current) => mergeTaskSnapshots(response.taskUpdates ?? [], current));
        }
        setFocusedTaskId(response.runId ?? response.taskUpdates?.[0]?.id ?? null);
        void refreshTaskSnapshot().catch(() => undefined);
        return;
      }

      appendFailedAssistantMessage(result.error?.message ?? "建议任务启动失败，请稍后再试。");
    } catch (error) { // broad-exception-boundary
      appendFailedAssistantMessage(readableError(error, "建议任务启动失败，请稍后再试。"));
    }
  }, [
    api,
    appendFailedAssistantMessage,
    ensureBackendReadyForTaskSubmit,
    mode,
    refreshTaskSnapshot,
    setFocusedTaskId,
    setMessages,
    setTasks
  ]);

  const submitHeroPrompt = useCallback(async () => {
    const value = draft.trim();
    if (!value || heroSubmitInFlight.current) return;

    heroSubmitInFlight.current = true;
    setHeroSubmitting(true);
    setHeroSubmitError(null);
    const result = await sendMessage(value);
    if (result.ok) {
      setDraft("");
    } else {
      setDraft(value);
      setHeroSubmitError(result.error ?? "任务没有启动成功，输入内容已保留，可以重试。");
    }
    setHeroSubmitting(false);
    heroSubmitInFlight.current = false;
  }, [draft, sendMessage]);

  const requestCleanupApproval = useCallback(async (scope: string) => {
    await sendMessage(
      `请基于这个文件范围生成清理确认任务：${scope}。先生成可清理项预览和审批请求；在我明确批准前不要移动或删除任何文件。`
    );
    void refreshTaskSnapshot().catch(() => undefined);
  }, [refreshTaskSnapshot, sendMessage]);

  return {
    draft,
    setDraft,
    heroSubmitting,
    heroSubmitError,
    sendMessage,
    executeSuggestion,
    submitHeroPrompt,
    requestCleanupApproval
  };
}
