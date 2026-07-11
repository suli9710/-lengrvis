import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Alert, AppState, type AppStateStatus } from "react-native";

import {
  AuthExpiredError,
  BackendHttpError,
  approvalWebSocketConnectionInfo,
  createMobileTask,
  disconnectMobileDevice,
  ForbiddenError,
  listMobileTasks,
  listPendingApprovals,
  registerMobilePushSubscription,
  submitMobileTaskCommand,
  submitMobileTaskFollowUp,
  unregisterMobilePushSubscription,
  type ApprovalEvent,
  type BackendApproval,
  type MobileTask,
  type MobileTaskMode,
  type MobileTaskTemplateId,
  type PairingSession,
  type RemoteInputGrant,
} from "../api/client";
import type { HomeSnapshot } from "../navigation/types";
import { requestApprovalPushSubscription } from "../notifications";
import { approvalPushRegistrationRetryDelayMs, ensureApprovalPushSubscription } from "../pushSubscriptionLifecycle";
import { isRemoteInputGrantUsable, remoteInputGrantDisplayStatus } from "../remoteInputGrant";
import { isMobileTaskActive, isMobileTaskTerminal } from "../taskCompanionDisplay";
import { taskStarterTemplates } from "../taskStarterTemplates";

type ApprovalConnection = "offline" | "connecting" | "online";
type TaskCommand = "pause" | "resume" | "cancel";

interface MobileCompanionContextValue {
  session: PairingSession;
  approvals: BackendApproval[];
  tasks: MobileTask[];
  activeTasks: MobileTask[];
  completedTasks: MobileTask[];
  connection: ApprovalConnection;
  error: string;
  hasLoadedOnce: boolean;
  isRefreshing: boolean;
  lastUpdatedAt: string;
  notificationsOff: boolean;
  pendingCount: number;
  remoteInputGrant: RemoteInputGrant | null;
  selectedTemplateId: MobileTaskTemplateId;
  taskDraft: string;
  taskMode: MobileTaskMode;
  isStartingTask: boolean;
  taskActionId: string;
  followUpTaskId: string;
  followUpDrafts: Record<string, string>;
  homeSnapshot: HomeSnapshot;
  setSelectedTemplateId: (templateId: MobileTaskTemplateId) => void;
  setTaskDraft: (value: string) => void;
  setTaskMode: (value: MobileTaskMode) => void;
  setFollowUpDraft: (taskId: string, value: string) => void;
  refreshAll: () => void;
  refreshTasks: () => void;
  disconnectPhone: () => void;
  submitMobileTemplateTask: () => void;
  submitTaskFollowUp: (task: MobileTask, instruction: string) => Promise<void>;
  submitTaskAction: (task: MobileTask, action: TaskCommand) => Promise<void>;
  onSelectApproval: (approval: BackendApproval) => void;
  onSessionExpired: () => void;
  onRemoteInputGrantRevoked: (grant: RemoteInputGrant) => void;
  updateApproval: (approval: BackendApproval) => void;
}

const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 30000;
const TASK_POLL_INTERVAL_MS = 12000;

const MobileCompanionContext = createContext<MobileCompanionContextValue | null>(null);

export function MobileCompanionProvider({
  children,
  session,
  remoteInputGrant,
  onRemoteInputGrant,
  onRemoteInputGrantRevoked,
  onSessionExpired,
  onSelectApproval,
}: {
  children: ReactNode;
  session: PairingSession;
  remoteInputGrant: RemoteInputGrant | null;
  onRemoteInputGrant: (grant: RemoteInputGrant) => void;
  onRemoteInputGrantRevoked: (grant: RemoteInputGrant) => void;
  onSessionExpired: () => void;
  onSelectApproval: (approval: BackendApproval) => void;
}) {
  const [approvals, setApprovals] = useState<BackendApproval[]>([]);
  const [connection, setConnection] = useState<ApprovalConnection>("offline");
  const [error, setError] = useState("");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState("");
  const [tasks, setTasks] = useState<MobileTask[]>([]);
  const [taskActionId, setTaskActionId] = useState("");
  const [selectedTemplateId, setSelectedTemplateIdState] = useState<MobileTaskTemplateId>("organize_downloads");
  const [taskDraft, setTaskDraft] = useState("");
  const [taskMode, setTaskMode] = useState<MobileTaskMode>("hybrid");
  const [isStartingTask, setIsStartingTask] = useState(false);
  const [followUpTaskId, setFollowUpTaskId] = useState("");
  const [followUpDrafts, setFollowUpDrafts] = useState<Record<string, string>>({});
  const [notificationsOff, setNotificationsOff] = useState(false);
  const [pushRegistrationAttempt, setPushRegistrationAttempt] = useState(0);
  const [streamReconnectKey, setStreamReconnectKey] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const registeredPushSubscriptionKeyRef = useRef("");

  const pendingCount = useMemo(
    () => approvals.filter((approval) => approval.status === "pending").length,
    [approvals],
  );
  const activeTasks = useMemo(() => tasks.filter((task) => !isMobileTaskTerminal(task)), [tasks]);
  const completedTasks = useMemo(() => tasks.filter(isMobileTaskTerminal), [tasks]);

  const upsertApproval = useCallback((approval: BackendApproval) => {
    setApprovals((current) => {
      const next = current.filter((item) => item.id !== approval.id);
      return [approval, ...next].sort((left, right) => right.created_at.localeCompare(left.created_at));
    });
  }, []);

  const handleAuthExpired = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    socketRef.current?.close();
    socketRef.current = null;
    onSessionExpired();
  }, [onSessionExpired]);

  const mergePendingApprovals = useCallback((pending: BackendApproval[]) => {
    setApprovals((current) => {
      const pendingIds = new Set(pending.map((approval) => approval.id));
      const decided = current.filter((approval) => approval.status !== "pending");
      const pendingWithoutStaleDecided = pending.filter(
        (approval) => !decided.some((decidedApproval) => decidedApproval.id === approval.id),
      );
      const decidedWithoutPending = decided.filter((approval) => !pendingIds.has(approval.id));
      const decidedWinningOverStalePending = decided.filter((approval) => pendingIds.has(approval.id));
      return [...pendingWithoutStaleDecided, ...decidedWinningOverStalePending, ...decidedWithoutPending].sort(
        (left, right) => right.created_at.localeCompare(left.created_at),
      );
    });
  }, []);

  const refreshTasksAsync = useCallback(async () => {
    const nextTasks = await listMobileTasks(session);
    setTasks(nextTasks);
    setLastUpdatedAt(new Date().toISOString());
  }, [session]);

  const refreshAllAsync = useCallback(async () => {
    const [pending, nextTasks] = await Promise.all([listPendingApprovals(session), listMobileTasks(session)]);
    mergePendingApprovals(pending);
    setTasks(nextTasks);
    setLastUpdatedAt(new Date().toISOString());
  }, [mergePendingApprovals, session]);

  const refreshAll = useCallback(() => {
    if (isRefreshing) return;
    setIsRefreshing(true);
    setError("");
    setStreamReconnectKey((current) => current + 1);
    void refreshAllAsync()
      .catch((currentError: unknown) => {
        if (currentError instanceof AuthExpiredError) {
          handleAuthExpired();
          return;
        }
        setError(errorMessage(currentError));
      })
      .finally(() => {
        setHasLoadedOnce(true);
        setIsRefreshing(false);
      });
  }, [handleAuthExpired, isRefreshing, refreshAllAsync]);

  const refreshTasks = useCallback(() => {
    void refreshTasksAsync().catch((currentError: unknown) => {
      if (currentError instanceof AuthExpiredError) {
        handleAuthExpired();
        return;
      }
      setError(errorMessage(currentError));
    });
  }, [handleAuthExpired, refreshTasksAsync]);

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current) return;
    const delay = Math.min(MAX_RECONNECT_DELAY_MS, INITIAL_RECONNECT_DELAY_MS * 2 ** reconnectAttemptRef.current);
    reconnectAttemptRef.current += 1;
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      setStreamReconnectKey((current) => current + 1);
    }, delay);
  }, []);

  useEffect(() => {
    let isActive = true;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    void ensureApprovalPushSubscription(
      session,
      {
        requestSubscription: requestApprovalPushSubscription,
        registerSubscription: registerMobilePushSubscription,
        unregisterSubscription: unregisterMobilePushSubscription,
      },
      registeredPushSubscriptionKeyRef.current,
    )
      .then((result) => {
        if (!isActive) return;
        if (result.status === "unavailable") {
          registeredPushSubscriptionKeyRef.current = "";
          setNotificationsOff(true);
          return;
        }
        registeredPushSubscriptionKeyRef.current = result.registrationKey;
        setPushRegistrationAttempt(0);
        setNotificationsOff(false);
      })
      .catch((currentError: unknown) => {
        if (!isActive) return;
        if (currentError instanceof AuthExpiredError) {
          handleAuthExpired();
          return;
        }
        // Notifications are optional: a provider or network failure never
        // blocks the paired approval stream or exposes provider diagnostics.
        setNotificationsOff(true);
        retryTimer = setTimeout(
          () => setPushRegistrationAttempt((attempt) => attempt + 1),
          approvalPushRegistrationRetryDelayMs(pushRegistrationAttempt),
        );
      });
    return () => {
      isActive = false;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [handleAuthExpired, pushRegistrationAttempt, session]);

  useEffect(() => {
    let closedByEffect = false;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    setConnection("connecting");
    setError("");
    void refreshAllAsync()
      .catch((currentError: unknown) => {
        if (currentError instanceof AuthExpiredError) {
          handleAuthExpired();
          return;
        }
        setError(errorMessage(currentError));
      })
      .finally(() => setHasLoadedOnce(true));

    let connectionInfo: ReturnType<typeof approvalWebSocketConnectionInfo>;
    try {
      connectionInfo = approvalWebSocketConnectionInfo(session);
    } catch (currentError) {
      if (currentError instanceof AuthExpiredError) {
        handleAuthExpired();
        return () => {
          closedByEffect = true;
        };
      }
      setConnection("offline");
      setError(errorMessage(currentError));
      return () => {
        closedByEffect = true;
      };
    }

    const socket = new WebSocket(connectionInfo.url, connectionInfo.protocols);
    socketRef.current = socket;

    socket.onopen = () => {
      reconnectAttemptRef.current = 0;
      setConnection("online");
      setError("");
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as ApprovalEvent;
        if (payload.type === "connected") {
          mergePendingApprovals(payload.pending);
          if (payload.tasks) setTasks(payload.tasks);
          const snapshotGrant = (payload.remote_input_grants ?? []).find((grant) => isRemoteInputGrantUsable(grant));
          if (snapshotGrant) onRemoteInputGrant(snapshotGrant);
          return;
        }
        if (payload.type === "approval_notification" || payload.type === "approval_created") {
          upsertApproval(payload.approval);
          return;
        }
        if (payload.type === "approval_decided") {
          upsertApproval(payload.approval);
          return;
        }
        if (payload.type === "remote_input_grant_created" && payload.device_id === session.deviceId) {
          onRemoteInputGrant(payload.grant);
          return;
        }
        if (payload.type === "remote_input_grant_revoked" && payload.device_id === session.deviceId) {
          onRemoteInputGrantRevoked(payload.grant);
          return;
        }
        if (payload.type === "mobile_device_revoked" && payload.device_id === session.deviceId) {
          handleAuthExpired();
        }
      } catch {
        // Polling remains available if a stream event is malformed.
      }
    };

    socket.onerror = () => {
      setError("无法保持与电脑的连接。请确认 Lengrvis 已打开，然后点刷新。");
    };

    socket.onclose = (event) => {
      if (event.code === 1008) {
        handleAuthExpired();
        return;
      }
      if (!closedByEffect) {
        setConnection("offline");
        scheduleReconnect();
      }
    };

    return () => {
      closedByEffect = true;
      if (socketRef.current === socket) socketRef.current = null;
      socket.close();
    };
  }, [handleAuthExpired, mergePendingApprovals, onRemoteInputGrant, onRemoteInputGrantRevoked, refreshAllAsync, scheduleReconnect, session, streamReconnectKey, upsertApproval]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state: AppStateStatus) => {
      if (state !== "active") return;
      setPushRegistrationAttempt((attempt) => attempt + 1);
      void refreshAllAsync()
        .catch((currentError: unknown) => {
          if (currentError instanceof AuthExpiredError) {
            handleAuthExpired();
            return;
          }
          setError(errorMessage(currentError));
        })
        .finally(() => setHasLoadedOnce(true));
      setStreamReconnectKey((current) => current + 1);
    });
    return () => subscription.remove();
  }, [handleAuthExpired, refreshAllAsync]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (AppState.currentState !== "active") return;
      void refreshTasksAsync().catch((currentError: unknown) => {
        if (currentError instanceof AuthExpiredError) {
          handleAuthExpired();
          return;
        }
        setError(errorMessage(currentError));
      });
    }, TASK_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [handleAuthExpired, refreshTasksAsync]);

  useEffect(
    () => () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    },
    [],
  );

  const disconnectPhone = useCallback(() => {
    Alert.alert("断开手机连接？", "之后仍可在电脑端 Lengrvis 重新连接。", [
      { text: "取消", style: "cancel" },
      {
        text: "断开连接",
        style: "destructive",
        onPress: () => {
          void (async () => {
            let disconnectError: unknown;
            try {
              await disconnectMobileDevice(session);
            } catch (currentError) {
              disconnectError = currentError;
            }
            socketRef.current?.close();
            socketRef.current = null;
            onSessionExpired();
            if (disconnectError) {
              Alert.alert("断开连接", disconnectErrorMessage(disconnectError));
            }
          })();
        },
      },
    ]);
  }, [onSessionExpired, session]);

  const setSelectedTemplateId = useCallback((templateId: MobileTaskTemplateId) => {
    setSelectedTemplateIdState(templateId);
    const manifest = taskStarterTemplates.find((template) => template.id === templateId);
    if (manifest) setTaskMode(manifest.mode);
  }, []);

  const setFollowUpDraft = useCallback((taskId: string, value: string) => {
    setFollowUpDrafts((current) => ({ ...current, [taskId]: value }));
  }, []);

  const submitMobileTemplateTask = useCallback(() => {
    if (isStartingTask) return;
    setIsStartingTask(true);
    setError("");
    void createMobileTask(session, {
      template_id: selectedTemplateId,
      user_input: taskDraft.trim(),
      mode: taskMode,
    })
      .then((result) => {
        setTasks((current) => [result.task, ...current.filter((item) => item.id !== result.task.id)]);
        setTaskDraft("");
      })
      .catch((currentError: unknown) => {
        if (currentError instanceof AuthExpiredError) {
          handleAuthExpired();
          return;
        }
        setError(taskRequestErrorMessage(currentError));
      })
      .finally(() => setIsStartingTask(false));
  }, [handleAuthExpired, isStartingTask, selectedTemplateId, session, taskDraft, taskMode]);

  const submitTaskFollowUp = useCallback(async (task: MobileTask, instruction: string) => {
    const trimmed = instruction.trim();
    if (!trimmed) {
      setError("请输入要补充给电脑任务的指令。");
      return;
    }
    setFollowUpTaskId(task.id);
    setError("");
    try {
      const result = await submitMobileTaskFollowUp(session, task.id, { instruction: trimmed });
      setTasks((current) => [result.task, ...current.filter((item) => item.id !== result.task.id)]);
      setFollowUpDrafts((current) => ({ ...current, [task.id]: "" }));
    } catch (currentError) {
      if (currentError instanceof AuthExpiredError) {
        handleAuthExpired();
        return;
      }
      setError(taskRequestErrorMessage(currentError));
      void refreshTasksAsync().catch(() => undefined);
    } finally {
      setFollowUpTaskId("");
    }
  }, [handleAuthExpired, refreshTasksAsync, session]);

  const submitTaskAction = useCallback(async (task: MobileTask, action: TaskCommand) => {
    const run = async () => {
      setTaskActionId(`${task.id}:${action}`);
      setError("");
      try {
        const nextTask = await submitMobileTaskCommand(session, task.id, action);
        setTasks((current) => [nextTask, ...current.filter((item) => item.id !== nextTask.id)]);
      } catch (currentError) {
        if (currentError instanceof AuthExpiredError) {
          handleAuthExpired();
          return;
        }
        setError(errorMessage(currentError));
        void refreshTasksAsync().catch(() => undefined);
      } finally {
        setTaskActionId("");
      }
    };
    if (action === "cancel") {
      Alert.alert("取消这项电脑任务？", "电脑端会停止后续执行，已完成的步骤会保留记录。", [
        { text: "继续保留", style: "cancel" },
        { text: "取消任务", style: "destructive", onPress: () => void run() },
      ]);
      return;
    }
    await run();
  }, [handleAuthExpired, refreshTasksAsync, session]);

  const remoteEntryStatus = remoteInputGrantDisplayStatus(remoteInputGrant);
  const runningTaskCount = useMemo(() => tasks.filter(isMobileTaskActive).length, [tasks]);
  const homeSnapshot = useMemo<HomeSnapshot>(() => {
    const remoteInputLabel = remoteEntryStatus.label;
    const nextStep = pendingCount > 0
      ? "先处理高风险审批；看不懂就拒绝或回电脑端确认。"
      : runningTaskCount > 0
        ? "有电脑任务正在执行，手机可随时暂停、补充或取消。"
        : "电脑端空闲，可以从手机发起一个低风险任务模板。";
    return {
      connectionLabel: connectionStatusText(connection),
      pendingApprovals: pendingCount,
      activeTasks: runningTaskCount,
      remoteInputLabel,
      nextStep,
    };
  }, [connection, pendingCount, remoteEntryStatus.label, runningTaskCount]);

  const value = useMemo<MobileCompanionContextValue>(() => ({
    session,
    approvals,
    tasks,
    activeTasks,
    completedTasks,
    connection,
    error,
    hasLoadedOnce,
    isRefreshing,
    lastUpdatedAt,
    notificationsOff,
    pendingCount,
    remoteInputGrant,
    selectedTemplateId,
    taskDraft,
    taskMode,
    isStartingTask,
    taskActionId,
    followUpTaskId,
    followUpDrafts,
    homeSnapshot,
    setSelectedTemplateId,
    setTaskDraft,
    setTaskMode,
    setFollowUpDraft,
    refreshAll,
    refreshTasks,
    disconnectPhone,
    submitMobileTemplateTask,
    submitTaskFollowUp,
    submitTaskAction,
    onSelectApproval,
    onSessionExpired,
    onRemoteInputGrantRevoked,
    updateApproval: upsertApproval,
  }), [
    activeTasks,
    approvals,
    completedTasks,
    connection,
    disconnectPhone,
    error,
    followUpDrafts,
    followUpTaskId,
    hasLoadedOnce,
    homeSnapshot,
    isRefreshing,
    isStartingTask,
    lastUpdatedAt,
    notificationsOff,
    onSelectApproval,
    onSessionExpired,
    onRemoteInputGrantRevoked,
    pendingCount,
    refreshAll,
    refreshTasks,
    remoteInputGrant,
    selectedTemplateId,
    session,
    setFollowUpDraft,
    setSelectedTemplateId,
    submitMobileTemplateTask,
    submitTaskAction,
    submitTaskFollowUp,
    taskActionId,
    taskDraft,
    taskMode,
    tasks,
  ]);

  return <MobileCompanionContext.Provider value={value}>{children}</MobileCompanionContext.Provider>;
}

export function useMobileCompanion(): MobileCompanionContextValue {
  const context = useContext(MobileCompanionContext);
  if (!context) throw new Error("useMobileCompanion must be used within MobileCompanionProvider");
  return context;
}

export function connectionStatusText(connection: ApprovalConnection): string {
  if (connection === "online") return "已连接";
  if (connection === "connecting") return "正在连接";
  return "离线，正在自动重连";
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.includes("Failed to fetch")) {
    return "无法连接到电脑。请确认 Lengrvis 已打开，然后点刷新。";
  }
  if (error instanceof ForbiddenError) {
    return "这台手机没有权限查看这些审批。请在电脑端重新配对后再试。";
  }
  if (error instanceof BackendHttpError && error.status === 404) {
    return "电脑端没有找到这些请求。请刷新后再试。";
  }
  if (error instanceof BackendHttpError && error.status >= 500) {
    return "电脑端暂时无法处理请求。请稍后刷新重试。";
  }
  return "无法更新请求。请点刷新重试。";
}

function taskRequestErrorMessage(error: unknown): string {
  if (error instanceof ForbiddenError) {
    return "这台手机没有权限发起或续写电脑任务。请在电脑端重新配对后再试。";
  }
  if (error instanceof Error && error.message.includes("Failed to fetch")) {
    return "无法连接到电脑。请确认 Lengrvis 已打开，然后重试。";
  }
  return "电脑端没有接受这次任务请求。请查看电脑端状态后重试。";
}

function disconnectErrorMessage(error: unknown): string {
  if (error instanceof AuthExpiredError) {
    return "电脑端已不再接受这台手机的登录。本地连接信息已清除。";
  }
  if (error instanceof ForbiddenError) {
    return "电脑端拒绝了断开请求，可能这台手机已不是当前配对设备。本地连接信息已清除。";
  }
  return "没能通知电脑端断开连接，可能电脑不在线。本地连接信息已清除。";
}
