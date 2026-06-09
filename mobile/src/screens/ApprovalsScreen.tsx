import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  type AppStateStatus,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import {
  BellOff,
  ChevronRight,
  ClipboardList,
  FileQuestion,
  FolderOpen,
  HardDrive,
  Monitor,
  Pause,
  Play,
  RefreshCcw,
  Send,
  ShieldCheck,
  Unlink,
  XCircle,
} from "lucide-react-native";
import type { ReactNode } from "react";

import {
  AuthExpiredError,
  BackendHttpError,
  approvalWebSocketConnectionInfo,
  createMobileTask,
  disconnectMobileDevice,
  ForbiddenError,
  listMobileTasks,
  listPendingApprovals,
  submitMobileTaskCommand,
  submitMobileTaskFollowUp,
  type ApprovalEvent,
  type BackendApproval,
  type MobileTaskMode,
  type MobileTaskTemplateId,
  type MobileTask,
  type PairingSession,
  type RemoteInputGrant,
} from "../api/client";
import { approvalListSafety } from "../approvalSafetyDisplay";
import { approvalStatusLabel, approvalTitle, formatPreview, shortDate } from "../format";
import { notifyApproval, requestNotificationPermission } from "../notifications";
import { isRemoteInputGrantUsable, remoteInputGrantDisplayStatus } from "../remoteInputGrant";
import { safeDisplayText, safePreviewText } from "../safeDisplay";
import {
  isMobileTaskActive,
  taskActionAllowed,
  taskCredibilityText,
  taskDisplaySummary,
  taskDisplayTitle,
  taskNextStepText,
  taskStatusBadgeIsDone,
  taskStatusBadgeText,
  taskStatusDetailText,
} from "../taskCompanionDisplay";
import { taskStarterTemplates } from "../taskStarterTemplates";

type ApprovalConnection = "offline" | "connecting" | "online";
const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 30000;
const TASK_POLL_INTERVAL_MS = 12000;
const MOBILE_TASK_TEMPLATES: Array<{
  id: MobileTaskTemplateId;
  label: string;
  placeholder: string;
  icon: ReactNode;
}> = [
  {
    id: "organize_downloads",
    label: "整理下载目录",
    placeholder: "可选：说明要整理哪个目录或规则。",
    icon: <FolderOpen size={14} color="#23313d" />,
  },
  {
    id: "summarize_local_docs",
    label: "总结本地文档",
    placeholder: "可选：指定文件夹、文件名或总结重点。",
    icon: <ClipboardList size={14} color="#23313d" />,
  },
  {
    id: "find_large_files",
    label: "查找大文件",
    placeholder: "可选：指定磁盘或大小阈值。",
    icon: <HardDrive size={14} color="#23313d" />,
  },
  {
    id: "check_computer_status",
    label: "检查电脑状态",
    placeholder: "可选：重点关注内存、磁盘、启动项等。",
    icon: <Monitor size={14} color="#23313d" />,
  },
  {
    id: "document_qa",
    label: "文档问答",
    placeholder: "请输入问题和文档范围，例如合同付款条款。",
    icon: <FileQuestion size={14} color="#23313d" />,
  },
];
const MOBILE_TASK_MODES: Array<{ value: MobileTaskMode; label: string }> = [
  { value: "hybrid", label: "混合" },
  { value: "privacy", label: "隐私" },
  { value: "efficiency", label: "快速" },
];

export function ApprovalsScreen({
  session,
  onSelectApproval,
  onOpenRemote,
  onRemoteInputGrant,
  onRemoteInputGrantRevoked,
  onUnpair,
  remoteInputGrant,
}: {
  session: PairingSession;
  onSelectApproval: (approval: BackendApproval) => void;
  onOpenRemote: () => void;
  onRemoteInputGrant: (grant: RemoteInputGrant) => void;
  onRemoteInputGrantRevoked: (grant: RemoteInputGrant) => void;
  onUnpair: () => void;
  remoteInputGrant: RemoteInputGrant | null;
}) {
  const [approvals, setApprovals] = useState<BackendApproval[]>([]);
  const [connection, setConnection] = useState<ApprovalConnection>("offline");
  const [error, setError] = useState("");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState("");
  const [tasks, setTasks] = useState<MobileTask[]>([]);
  const [taskActionId, setTaskActionId] = useState("");
  const [selectedTemplateId, setSelectedTemplateId] = useState<MobileTaskTemplateId>("organize_downloads");
  const [taskDraft, setTaskDraft] = useState("");
  const [taskMode, setTaskMode] = useState<MobileTaskMode>("hybrid");
  const [isStartingTask, setIsStartingTask] = useState(false);
  const [followUpTaskId, setFollowUpTaskId] = useState("");
  const [followUpDrafts, setFollowUpDrafts] = useState<Record<string, string>>({});
  const [notificationsOff, setNotificationsOff] = useState(false);
  const [streamReconnectKey, setStreamReconnectKey] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);

  const pendingCount = useMemo(
    () => approvals.filter((approval) => approval.status === "pending").length,
    [approvals],
  );
  const headerTitle = pendingCount === 0 ? "暂无待处理" : `${pendingCount} 项待审批`;
  const activeTaskCount = useMemo(() => tasks.filter(isMobileTaskActive).length, [tasks]);
  const visibleTasks = useMemo(() => tasks.slice(0, 3), [tasks]);
  const isInitialLoading = !hasLoadedOnce && approvals.length === 0 && tasks.length === 0;
  const lastUpdatedText = lastUpdatedAt ? `上次同步 ${shortDate(lastUpdatedAt)}` : "等待首次同步";
  const selectedTemplate = useMemo(
    () => MOBILE_TASK_TEMPLATES.find((template) => template.id === selectedTemplateId) ?? MOBILE_TASK_TEMPLATES[0],
    [selectedTemplateId],
  );
  const selectedTemplateManifest = useMemo(
    () => taskStarterTemplates.find((template) => template.id === selectedTemplateId),
    [selectedTemplateId],
  );
  const remoteEntryStatus = remoteInputGrantDisplayStatus(remoteInputGrant);

  const selectTaskTemplate = (templateId: MobileTaskTemplateId) => {
    setSelectedTemplateId(templateId);
    const manifest = taskStarterTemplates.find((template) => template.id === templateId);
    if (manifest) {
      setTaskMode(manifest.mode);
    }
  };

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
    onUnpair();
  }, [onUnpair]);

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

  const refreshTasks = useCallback(async () => {
    const nextTasks = await listMobileTasks(session);
    setTasks(nextTasks);
    setLastUpdatedAt(new Date().toISOString());
  }, [session]);

  const refreshAll = useCallback(async () => {
    const [pending, nextTasks] = await Promise.all([listPendingApprovals(session), listMobileTasks(session)]);
    mergePendingApprovals(pending);
    setTasks(nextTasks);
    setLastUpdatedAt(new Date().toISOString());
  }, [mergePendingApprovals, session]);

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
    void requestNotificationPermission()
      .then((allowed) => setNotificationsOff(!allowed))
      .catch(() => setNotificationsOff(true));
  }, []);

  useEffect(() => {
    let closedByEffect = false;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    setConnection("connecting");
    setError("");
    void refreshAll()
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
          const snapshotGrant = (payload.remote_input_grants ?? []).find((grant) => isRemoteInputGrantUsable(grant));
          if (snapshotGrant) onRemoteInputGrant(snapshotGrant);
          return;
        }
        if (payload.type === "approval_notification" || payload.type === "approval_created") {
          upsertApproval(payload.approval);
          void notifyApproval(payload.approval);
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
  }, [handleAuthExpired, mergePendingApprovals, onRemoteInputGrant, onRemoteInputGrantRevoked, refreshAll, scheduleReconnect, session, streamReconnectKey, upsertApproval]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state: AppStateStatus) => {
      if (state !== "active") return;
      void refreshAll()
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
  }, [handleAuthExpired, refreshAll]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (AppState.currentState !== "active") return;
      void refreshTasks().catch((currentError: unknown) => {
        if (currentError instanceof AuthExpiredError) {
          handleAuthExpired();
          return;
        }
        setError(errorMessage(currentError));
      });
    }, TASK_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [handleAuthExpired, refreshTasks]);

  useEffect(
    () => () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    },
    [],
  );

  const disconnectPhone = async () => {
    let disconnectError: unknown;
    try {
      await disconnectMobileDevice(session);
    } catch (currentError) {
      disconnectError = currentError;
    }
    socketRef.current?.close();
    socketRef.current = null;
    onUnpair();
    if (disconnectError) {
      Alert.alert("断开连接", disconnectErrorMessage(disconnectError));
    }
  };

  const handleUnpair = () => {
    Alert.alert("断开手机连接？", "之后仍可在电脑端 Lengrvis 重新连接。", [
      { text: "取消", style: "cancel" },
      { text: "断开连接", onPress: () => void disconnectPhone(), style: "destructive" },
    ]);
  };

  const handleRefresh = () => {
    if (isRefreshing) return;
    setIsRefreshing(true);
    setError("");
    setStreamReconnectKey((current) => current + 1);
    void refreshAll()
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
  };

  const submitMobileTemplateTask = async () => {
    if (isStartingTask) return;
    setIsStartingTask(true);
    setError("");
    try {
      const result = await createMobileTask(session, {
        template_id: selectedTemplateId,
        user_input: taskDraft.trim(),
        mode: taskMode,
      });
      setTasks((current) => [result.task, ...current.filter((item) => item.id !== result.task.id)]);
      setTaskDraft("");
    } catch (currentError) {
      if (currentError instanceof AuthExpiredError) {
        handleAuthExpired();
        return;
      }
      setError(taskRequestErrorMessage(currentError));
    } finally {
      setIsStartingTask(false);
    }
  };

  const submitTaskFollowUp = async (task: MobileTask, instruction: string) => {
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
      void refreshTasks().catch(() => undefined);
    } finally {
      setFollowUpTaskId("");
    }
  };

  const submitTaskAction = async (task: MobileTask, action: "pause" | "resume" | "cancel") => {
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
        void refreshTasks().catch(() => undefined);
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
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor="#f6f4ee" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={styles.keyboardAvoider}>
        <View style={styles.header}>
          <View>
            <Text style={styles.kicker}>{connection === "online" ? "已连接电脑" : "正在连接"}</Text>
            <Text style={styles.headerTitle}>{headerTitle}</Text>
          </View>
          <View style={styles.headerActions}>
            <IconButton accessibilityLabel="查看电脑屏幕" icon={<Monitor size={18} color="#23313d" />} onPress={onOpenRemote} />
            <IconButton accessibilityLabel="刷新请求" icon={<RefreshCcw size={18} color="#23313d" />} onPress={handleRefresh} />
            <IconButton accessibilityLabel="断开手机连接" icon={<Unlink size={18} color="#8c2f39" />} onPress={handleUnpair} />
          </View>
        </View>

        <FlatList
          contentContainerStyle={approvals.length ? styles.list : styles.emptyList}
          data={approvals}
          keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
          keyboardShouldPersistTaps="handled"
          keyExtractor={(approval) => approval.id}
          ListHeaderComponent={(
          <View style={styles.listHeader}>
            <View style={styles.statusRow}>
              <ShieldCheck size={16} color={connection === "online" ? "#1f7a4d" : "#a46a00"} />
              <Text style={styles.statusText}>{connectionStatusText(connection)}</Text>
            </View>
            {notificationsOff ? (
              <View style={styles.noticeRow}>
                <BellOff size={16} color="#7a5700" />
                <Text style={styles.noticeText}>手机通知已关闭。请保持此页面打开，或点击刷新查看请求。</Text>
              </View>
            ) : null}
            {error ? <Text style={styles.errorBanner}>{error}</Text> : null}
            <View style={styles.companionPanel}>
              <View style={styles.companionHeader}>
                <View>
                  <Text style={styles.companionKicker}>任务助手</Text>
                  <Text style={styles.companionTitle}>{activeTaskCount ? `${activeTaskCount} 项电脑任务在进行` : "电脑端当前空闲"}</Text>
                </View>
                <Text style={styles.companionBadge}>{visibleTasks.length ? `${visibleTasks.length} 项` : "待命"}</Text>
              </View>
              <View style={styles.launchBox}>
                <View style={styles.templateGrid}>
                  {MOBILE_TASK_TEMPLATES.map((template) => {
                    const selected = template.id === selectedTemplateId;
                    return (
                      <Pressable
                        key={template.id}
                        accessibilityLabel={`任务模板：${template.label}`}
                        accessibilityRole="button"
                        accessibilityState={{ selected }}
                        hitSlop={4}
                        onPress={() => selectTaskTemplate(template.id)}
                        style={({ pressed }) => [styles.templateButton, selected && styles.templateButtonSelected, pressed && styles.pressed]}
                      >
                        {template.icon}
                        <Text style={[styles.templateButtonText, selected && styles.templateButtonTextSelected]}>{template.label}</Text>
                      </Pressable>
                    );
                  })}
                </View>
                <TextInput
                  accessibilityLabel="任务补充说明"
                  multiline
                  onChangeText={setTaskDraft}
                  placeholder={selectedTemplateManifest?.inputHint ?? selectedTemplate.placeholder}
                  placeholderTextColor="#7b8791"
                  style={styles.launchInput}
                  value={taskDraft}
                />
                <View style={styles.launchFooter}>
                  <View style={styles.modePicker}>
                    {MOBILE_TASK_MODES.map((mode) => {
                      const selected = mode.value === taskMode;
                      return (
                        <Pressable
                          key={mode.value}
                          accessibilityLabel={`任务模式：${mode.label}`}
                          accessibilityRole="button"
                          accessibilityState={{ selected }}
                          hitSlop={4}
                          onPress={() => setTaskMode(mode.value)}
                          style={({ pressed }) => [styles.modeButton, selected && styles.modeButtonSelected, pressed && styles.pressed]}
                        >
                          <Text style={[styles.modeButtonText, selected && styles.modeButtonTextSelected]}>{mode.label}</Text>
                        </Pressable>
                      );
                    })}
                  </View>
                  <Pressable
                    accessibilityLabel={isStartingTask ? "正在发起任务" : "发起任务"}
                    accessibilityRole="button"
                    accessibilityState={{ busy: isStartingTask, disabled: isStartingTask }}
                    disabled={isStartingTask}
                    hitSlop={4}
                    onPress={() => void submitMobileTemplateTask()}
                    style={({ pressed }) => [styles.launchButton, isStartingTask && styles.disabledAction, pressed && styles.pressed]}
                  >
                    <Send size={15} color="#ffffff" />
                    <Text style={styles.launchButtonText}>{isStartingTask ? "发起中" : "发起任务"}</Text>
                  </Pressable>
                </View>
              </View>
              {visibleTasks.length ? (
                <View style={styles.taskList}>
                  {visibleTasks.map((task) => (
                    <TaskCompanionCard
                      key={task.id}
                      actionId={taskActionId}
                      followUpBusy={followUpTaskId === task.id}
                      followUpValue={followUpDrafts[task.id] ?? ""}
                      onAction={submitTaskAction}
                      onFollowUp={submitTaskFollowUp}
                      onFollowUpTextChange={(text) => setFollowUpDrafts((current) => ({ ...current, [task.id]: text }))}
                      task={task}
                    />
                  ))}
                </View>
              ) : (
                <Text style={styles.companionEmpty}>在电脑端启动任务后，这里会显示安全摘要、可信度和下一步。</Text>
              )}
            </View>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`查看电脑屏幕，${remoteEntryStatus.label}，${remoteEntryStatus.detail}`}
              accessibilityHint={remoteEntryStatus.isActive ? "打开远程屏幕；输入仍需电脑端审批。" : "打开远程屏幕，只读查看电脑画面。"}
              hitSlop={4}
              onPress={onOpenRemote}
              style={({ pressed }) => [styles.remoteEntry, pressed && styles.pressed]}
            >
              <Monitor size={16} color="#23313d" />
              <View style={styles.remoteEntryCopy}>
                <Text style={styles.remoteEntryText}>查看电脑屏幕</Text>
                <Text numberOfLines={2} style={[styles.remoteEntryMeta, remoteEntryStatus.isActive && styles.remoteEntryMetaActive]}>
                  {remoteEntryStatus.detail}
                </Text>
              </View>
              <ChevronRight size={17} color="#65717c" />
            </Pressable>
            <Text style={styles.listSyncText}>{lastUpdatedText}</Text>
          </View>
          )}
          ListEmptyComponent={
            <EmptyApprovalsState hasError={Boolean(error)} isLoading={isInitialLoading} onRefresh={handleRefresh} />
          }
          onRefresh={handleRefresh}
          renderItem={({ item }) => <ApprovalCard approval={item} remoteInputGrant={remoteInputGrant} session={session} onPress={() => onSelectApproval(item)} />}
          refreshing={isRefreshing}
          style={styles.listViewport}
        />
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function EmptyApprovalsState({
  hasError,
  isLoading,
  onRefresh,
}: {
  hasError: boolean;
  isLoading: boolean;
  onRefresh: () => void;
}) {
  if (isLoading) {
    return (
      <View accessible accessibilityLabel="正在加载审批和任务" style={styles.emptyState}>
        <ActivityIndicator color="#0e5f76" />
        <Text style={styles.emptyTitle}>正在同步</Text>
        <Text style={styles.emptyText}>手机正在向电脑端加载审批和任务状态。</Text>
      </View>
    );
  }
  if (hasError) {
    return (
      <View accessibilityRole="alert" style={styles.emptyState}>
        <XCircle size={34} color="#8c2f39" />
        <Text style={styles.emptyTitle}>暂时连不上电脑</Text>
        <Text style={styles.emptyText}>请确认电脑端 Lengrvis 已打开，然后重新同步。</Text>
        <Pressable
          accessibilityLabel="重新同步审批和任务"
          accessibilityRole="button"
          hitSlop={4}
          onPress={onRefresh}
          style={({ pressed }) => [styles.emptyRetryButton, pressed && styles.pressed]}
        >
          <RefreshCcw size={15} color="#23313d" />
          <Text style={styles.emptyRetryText}>重新同步</Text>
        </Pressable>
      </View>
    );
  }
  return (
    <View style={styles.emptyState}>
      <ShieldCheck size={34} color="#5f6b76" />
      <Text style={styles.emptyTitle}>暂无待处理</Text>
      <Text style={styles.emptyText}>电脑端的新审批请求会显示在这里。</Text>
    </View>
  );
}

function ApprovalCard({
  approval,
  remoteInputGrant,
  session,
  onPress,
}: {
  approval: BackendApproval;
  remoteInputGrant: RemoteInputGrant | null;
  session: PairingSession;
  onPress: () => void;
}) {
  const pending = approval.status === "pending";
  const preview = readablePreview(approval.diff_preview);
  const message = safeDisplayText(approval.message, "打开后查看这项审批。");
  const activeRemoteInputGrant = isRemoteInputGrantUsable(remoteInputGrant) ? remoteInputGrant : null;
  const safety = approvalListSafety(
    approval,
    activeRemoteInputGrant ? { deviceId: session.deviceId, grantId: activeRemoteInputGrant.id, bindingRef: activeRemoteInputGrant.binding_ref } : null,
  );
  const safetyStyle = safety.tone === "danger" ? styles.safetyDanger : safety.tone === "warning" ? styles.safetyWarning : styles.safetySafe;
  return (
    <Pressable
      accessibilityHint={safety.approveBlockedReason ? "打开详情后仍只能拒绝或回电脑端处理。" : "打开查看审批详情和安全核对。"}
      accessibilityLabel={`${approvalCardTitle(approval)}，${approvalStatusText(approval)}，${safety.label}`}
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
    >
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleWrap}>
          <Text style={styles.cardTitle}>{approvalCardTitle(approval)}</Text>
          <Text style={styles.cardMeta}>{shortDate(approval.created_at)}</Text>
        </View>
        <View style={styles.cardStatus}>
          <Text
            style={[
              styles.badge,
              pending ? styles.badgePending : styles.badgeDone,
              pending && safety.tone === "danger" && styles.badgeDanger,
              pending && safety.tone === "warning" && styles.badgeWarning,
              pending && safety.tone === "safe" && styles.badgeSafe,
            ]}
          >
            {pending ? safety.label : approvalStatusText(approval)}
          </Text>
          <ChevronRight size={18} color="#65717c" />
        </View>
      </View>
      <Text style={styles.message}>{message}</Text>
      <View style={[styles.safetyCallout, safetyStyle]}>
        <Text style={styles.safetyLabel}>{safety.label}</Text>
        <Text numberOfLines={2} style={styles.safetyDetail}>{safety.detail}</Text>
      </View>
      <Text numberOfLines={3} style={styles.preview}>{preview}</Text>
    </Pressable>
  );
}

function TaskCompanionCard({
  task,
  actionId,
  followUpBusy,
  followUpValue,
  onAction,
  onFollowUp,
  onFollowUpTextChange,
}: {
  task: MobileTask;
  actionId: string;
  followUpBusy: boolean;
  followUpValue: string;
  onAction: (task: MobileTask, action: "pause" | "resume" | "cancel") => Promise<void>;
  onFollowUp: (task: MobileTask, instruction: string) => Promise<void>;
  onFollowUpTextChange: (text: string) => void;
}) {
  const badgeDone = taskStatusBadgeIsDone(task);
  const actionBusy = actionId.startsWith(`${task.id}:`);
  const title = taskDisplayTitle(task);
  const summary = taskDisplaySummary(task);
  const statusDetail = taskStatusDetailText(task);
  const credibility = taskCredibilityText(task);
  const nextStep = taskNextStepText(task);
  const followUpAllowed = taskActionAllowed(task, "follow_up");
  const followUpDisabled = followUpBusy || !followUpValue.trim() || !followUpAllowed;
  const showResume = task.status === "paused" || taskActionAllowed(task, "resume");
  return (
    <View style={styles.taskCard}>
      <View style={styles.taskCardHeader}>
        <View style={styles.taskCardTitleWrap}>
          <Text numberOfLines={2} style={styles.taskCardTitle}>{title}</Text>
          <Text style={styles.taskCardMeta}>{taskModeText(task.mode)} · {shortDate(task.updated_at)}</Text>
        </View>
        <Text style={[styles.badge, badgeDone ? styles.badgeDone : styles.badgePending]}>{taskStatusBadgeText(task)}</Text>
      </View>
      <Text numberOfLines={3} style={styles.taskSummary}>{summary}</Text>
      <Text style={styles.taskStatusDetail}>{statusDetail}</Text>
      <View style={styles.taskSignalBlock}>
        <Text style={styles.taskSignalLabel}>可信度</Text>
        <Text style={styles.taskSignalText}>{credibility}</Text>
        <Text style={styles.taskSignalLabel}>安全下一步</Text>
        <Text style={styles.taskSignalText}>{nextStep}</Text>
      </View>
      <View style={styles.followUpRow}>
        <TextInput
          accessibilityLabel="补充任务指令"
          editable={followUpAllowed && !followUpBusy}
          multiline
          onChangeText={onFollowUpTextChange}
          placeholder={
            followUpAllowed
              ? task.mode === "privacy"
                ? "补充隐私任务指令，内容只发给电脑端。"
                : "补充下一步，不要输入密码或 token。"
              : "此任务当前不能补充指令。"
          }
          placeholderTextColor="#7b8791"
          style={[styles.followUpInput, !followUpAllowed && styles.disabledInput]}
          value={followUpValue}
        />
        <Pressable
          accessibilityLabel="发送补充任务指令"
          accessibilityRole="button"
          accessibilityState={{ busy: followUpBusy, disabled: followUpDisabled }}
          disabled={followUpDisabled}
          hitSlop={4}
          onPress={() => void onFollowUp(task, followUpValue)}
          style={({ pressed }) => [styles.followUpButton, followUpDisabled && styles.disabledAction, pressed && styles.pressed]}
        >
          <Send size={14} color="#ffffff" />
        </Pressable>
      </View>
      <View style={styles.taskActions}>
        {showResume ? (
          <TaskActionButton
            disabled={actionBusy || !taskActionAllowed(task, "resume")}
            icon={<Play size={14} color="#1f7a4d" />}
            label="继续"
            onPress={() => void onAction(task, "resume")}
          />
        ) : (
          <TaskActionButton
            disabled={actionBusy || !taskActionAllowed(task, "pause")}
            icon={<Pause size={14} color="#6c5a1b" />}
            label="暂停"
            onPress={() => void onAction(task, "pause")}
          />
        )}
        <TaskActionButton
          disabled={actionBusy || !taskActionAllowed(task, "cancel")}
          icon={<XCircle size={14} color="#8c2f39" />}
          label="取消"
          onPress={() => void onAction(task, "cancel")}
        />
      </View>
    </View>
  );
}

function TaskActionButton({
  disabled,
  icon,
  label,
  onPress,
}: {
  disabled: boolean;
  icon: ReactNode;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      hitSlop={4}
      onPress={onPress}
      style={({ pressed }) => [styles.taskAction, disabled && styles.disabledAction, pressed && styles.pressed]}
    >
      {icon}
      <Text style={styles.taskActionText}>{label}</Text>
    </Pressable>
  );
}

function IconButton({
  accessibilityLabel,
  icon,
  onPress,
}: {
  accessibilityLabel: string;
  icon: ReactNode;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="button"
      hitSlop={4}
      onPress={onPress}
      style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
    >
      {icon}
    </Pressable>
  );
}

function errorMessage(error: unknown): string {
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

function connectionStatusText(connection: ApprovalConnection): string {
  if (connection === "online") return "已连接";
  if (connection === "connecting") return "正在连接";
  return "离线，正在自动重连";
}

function approvalStatusText(approval: BackendApproval): string {
  if (approval.status === "pending") return "待审批";
  return approvalStatusLabel(approval.status);
}

function approvalCardTitle(approval: BackendApproval): string {
  if (approval.approval_type === "tool_call") return "审批请求";
  return approvalTitle(approval).replace("工具审批", "审批请求");
}

function readablePreview(value: unknown): string {
  return safePreviewText(formatPreview(value));
}

function taskModeText(mode: string): string {
  if (mode === "privacy") return "隐私";
  if (mode === "hybrid") return "混合";
  return "快速";
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f6f4ee",
  },
  keyboardAvoider: {
    flex: 1,
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 18,
    paddingBottom: 12,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  kicker: {
    color: "#65717c",
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  headerTitle: {
    color: "#1f2933",
    fontSize: 30,
    fontWeight: "800",
    marginTop: 2,
  },
  headerActions: {
    flexDirection: "row",
    gap: 8,
  },
  iconButton: {
    width: 48,
    height: 48,
    borderRadius: 8,
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "#d7dedf",
  },
  statusRow: {
    marginHorizontal: 20,
    minHeight: 38,
    borderRadius: 8,
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#d7dedf",
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    gap: 8,
  },
  statusText: {
    color: "#3a4651",
    fontWeight: "700",
  },
  noticeRow: {
    marginHorizontal: 20,
    marginTop: 8,
    minHeight: 38,
    borderRadius: 8,
    backgroundColor: "#fff8df",
    borderWidth: 1,
    borderColor: "#ead89e",
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    gap: 8,
  },
  noticeText: {
    flex: 1,
    color: "#5f4a08",
    lineHeight: 20,
  },
  errorBanner: {
    marginHorizontal: 20,
    marginTop: 10,
    color: "#8c2f39",
    lineHeight: 20,
  },
  companionPanel: {
    marginHorizontal: 20,
    marginTop: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d7dedf",
    backgroundColor: "#ffffff",
    padding: 12,
    gap: 10,
  },
  companionHeader: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 10,
  },
  companionKicker: {
    color: "#65717c",
    fontSize: 11,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  companionTitle: {
    color: "#1f2933",
    fontSize: 17,
    fontWeight: "900",
    marginTop: 2,
  },
  companionBadge: {
    minHeight: 24,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 7,
    overflow: "hidden",
    color: "#1f7a4d",
    backgroundColor: "#e7f6ef",
    fontSize: 11,
    fontWeight: "900",
  },
  companionEmpty: {
    color: "#5f6b76",
    lineHeight: 20,
  },
  launchBox: {
    gap: 10,
  },
  templateGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  templateButton: {
    minHeight: 48,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d7dedf",
    backgroundColor: "#f8fafb",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 9,
    paddingVertical: 6,
  },
  templateButtonSelected: {
    borderColor: "#8fb9a0",
    backgroundColor: "#e7f6ef",
  },
  templateButtonText: {
    color: "#23313d",
    fontSize: 12,
    fontWeight: "800",
  },
  templateButtonTextSelected: {
    color: "#1f7a4d",
  },
  launchInput: {
    minHeight: 58,
    maxHeight: 92,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d7dedf",
    backgroundColor: "#f8fafb",
    color: "#23313d",
    paddingHorizontal: 10,
    paddingVertical: 8,
    textAlignVertical: "top",
    lineHeight: 19,
  },
  launchFooter: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
  },
  modePicker: {
    flex: 1,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  modeButton: {
    minHeight: 48,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d7dedf",
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 10,
  },
  modeButtonSelected: {
    borderColor: "#a7b8d8",
    backgroundColor: "#eef3ff",
  },
  modeButtonText: {
    color: "#46535f",
    fontSize: 12,
    fontWeight: "800",
  },
  modeButtonTextSelected: {
    color: "#234a92",
  },
  launchButton: {
    minHeight: 48,
    borderRadius: 8,
    backgroundColor: "#1f7a4d",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingHorizontal: 12,
  },
  launchButtonText: {
    color: "#ffffff",
    fontSize: 12,
    fontWeight: "900",
  },
  taskList: {
    gap: 8,
  },
  taskCard: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#e1e6e8",
    backgroundColor: "#f8fafb",
    padding: 10,
    gap: 8,
  },
  taskCardHeader: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 8,
  },
  taskCardTitleWrap: {
    flex: 1,
    minWidth: 0,
  },
  taskCardTitle: {
    color: "#23313d",
    fontSize: 14,
    fontWeight: "900",
    lineHeight: 18,
  },
  taskCardMeta: {
    color: "#65717c",
    fontSize: 11,
    fontWeight: "700",
    marginTop: 2,
  },
  taskSummary: {
    color: "#4b5964",
    fontSize: 12,
    lineHeight: 18,
  },
  taskStatusDetail: {
    color: "#65717c",
    fontSize: 12,
    lineHeight: 18,
  },
  taskSignalBlock: {
    gap: 3,
  },
  taskSignalLabel: {
    color: "#65717c",
    fontSize: 10,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  taskSignalText: {
    color: "#27343f",
    fontSize: 12,
    lineHeight: 18,
  },
  followUpRow: {
    flexDirection: "row",
    alignItems: "stretch",
    gap: 8,
  },
  followUpInput: {
    flex: 1,
    minHeight: 38,
    maxHeight: 74,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d7dedf",
    backgroundColor: "#ffffff",
    color: "#23313d",
    fontSize: 12,
    lineHeight: 18,
    paddingHorizontal: 9,
    paddingVertical: 7,
    textAlignVertical: "top",
  },
  disabledInput: {
    backgroundColor: "#eef2f3",
    color: "#65717c",
  },
  followUpButton: {
    width: 48,
    minHeight: 48,
    borderRadius: 8,
    backgroundColor: "#1f7a4d",
    alignItems: "center",
    justifyContent: "center",
  },
  taskActions: {
    flexDirection: "row",
    gap: 8,
  },
  taskAction: {
    minHeight: 48,
    minWidth: 86,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d7dedf",
    backgroundColor: "#ffffff",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingHorizontal: 10,
  },
  taskActionText: {
    color: "#23313d",
    fontSize: 12,
    fontWeight: "900",
  },
  disabledAction: {
    opacity: 0.44,
  },
  remoteEntry: {
    marginHorizontal: 20,
    marginTop: 10,
    minHeight: 48,
    borderRadius: 8,
    backgroundColor: "#eef5f2",
    borderWidth: 1,
    borderColor: "#cddbd3",
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    gap: 8,
  },
  remoteEntryText: {
    color: "#23313d",
    fontWeight: "800",
  },
  remoteEntryCopy: {
    flex: 1,
    minWidth: 0,
  },
  remoteEntryMeta: {
    color: "#65717c",
    fontSize: 12,
    lineHeight: 17,
    marginTop: 2,
  },
  remoteEntryMetaActive: {
    color: "#1f6244",
  },
  listHeader: {
    paddingBottom: 4,
  },
  list: {
    paddingTop: 0,
    paddingBottom: Platform.select({ android: 96, default: 28 }),
    gap: 14,
  },
  emptyList: {
    flexGrow: 1,
    paddingTop: 0,
    paddingBottom: Platform.select({ android: 96, default: 28 }),
  },
  listViewport: {
    flex: 1,
  },
  listFooter: {
    marginTop: 14,
  },
  listSyncText: {
    marginHorizontal: 20,
    marginTop: 12,
    color: "#65717c",
    fontSize: 12,
    fontWeight: "700",
    marginBottom: 10,
  },
  emptyState: {
    alignItems: "center",
    gap: 10,
    paddingTop: 90,
  },
  emptyTitle: {
    color: "#1f2933",
    fontSize: 20,
    fontWeight: "800",
  },
  emptyText: {
    color: "#5f6b76",
    textAlign: "center",
    lineHeight: 22,
  },
  emptyRetryButton: {
    minHeight: 48,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d7dedf",
    backgroundColor: "#ffffff",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
    paddingHorizontal: 12,
  },
  emptyRetryText: {
    color: "#23313d",
    fontSize: 12,
    fontWeight: "900",
  },
  card: {
    marginHorizontal: 20,
    borderRadius: 8,
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#d7dedf",
    padding: 16,
    gap: 12,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 12,
  },
  cardTitleWrap: {
    flex: 1,
    minWidth: 0,
  },
  cardTitle: {
    color: "#1f2933",
    fontSize: 18,
    fontWeight: "800",
  },
  cardMeta: {
    color: "#65717c",
    marginTop: 3,
  },
  cardStatus: {
    flexShrink: 1,
    maxWidth: "48%",
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  badge: {
    flexShrink: 1,
    maxWidth: "100%",
    borderRadius: 8,
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 5,
    fontSize: 12,
    fontWeight: "800",
  },
  badgePending: {
    backgroundColor: "#fff2c6",
    color: "#7a5700",
  },
  badgeDone: {
    backgroundColor: "#e7ece8",
    color: "#1f6244",
  },
  badgeDanger: {
    backgroundColor: "#f9d8dc",
    color: "#8c2f39",
  },
  badgeWarning: {
    backgroundColor: "#fff2c6",
    color: "#7a5700",
  },
  badgeSafe: {
    backgroundColor: "#dff3e8",
    color: "#1f6244",
  },
  message: {
    color: "#27343f",
    lineHeight: 22,
    fontSize: 15,
  },
  safetyCallout: {
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 8,
    gap: 3,
  },
  safetySafe: {
    borderColor: "#bdd8c9",
    backgroundColor: "#eef8f2",
  },
  safetyWarning: {
    borderColor: "#e4cf8b",
    backgroundColor: "#fff9e8",
  },
  safetyDanger: {
    borderColor: "#e1b8be",
    backgroundColor: "#fff5f6",
  },
  safetyLabel: {
    color: "#23313d",
    fontSize: 12,
    fontWeight: "900",
  },
  safetyDetail: {
    color: "#46535f",
    fontSize: 12,
    lineHeight: 17,
  },
  preview: {
    color: "#46535f",
    backgroundColor: "#f3f6f7",
    borderRadius: 8,
    padding: 12,
    lineHeight: 20,
  },
  pressed: {
    opacity: 0.72,
  },
});
