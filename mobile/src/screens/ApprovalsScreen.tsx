import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  FlatList,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { BellOff, ChevronRight, RefreshCcw, ShieldCheck, Unlink } from "lucide-react-native";
import type { ReactNode } from "react";

import {
  AuthExpiredError,
  approvalWebSocketUrl,
  listPendingApprovals,
  mobileAuthWebSocketProtocols,
  type ApprovalEvent,
  type BackendApproval,
  type PairingSession,
} from "../api/client";
import { approvalStatusLabel, approvalTitle, formatPreview, shortDate } from "../format";
import { notifyApproval, requestNotificationPermission } from "../notifications";
import { clearSession } from "../store/auth";

type ApprovalConnection = "offline" | "connecting" | "online";

export function ApprovalsScreen({
  session,
  onSelectApproval,
  onUnpair,
}: {
  session: PairingSession;
  onSelectApproval: (approval: BackendApproval) => void;
  onUnpair: () => void;
}) {
  const [approvals, setApprovals] = useState<BackendApproval[]>([]);
  const [connection, setConnection] = useState<ApprovalConnection>("offline");
  const [error, setError] = useState("");
  const [notificationsOff, setNotificationsOff] = useState(false);
  const [streamReconnectKey, setStreamReconnectKey] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);

  const pendingCount = useMemo(
    () => approvals.filter((approval) => approval.status === "pending").length,
    [approvals],
  );
  const headerTitle = pendingCount === 0 ? "暂无待处理" : `${pendingCount} 项待审批`;

  const upsertApproval = useCallback((approval: BackendApproval) => {
    setApprovals((current) => {
      const next = current.filter((item) => item.id !== approval.id);
      return [approval, ...next].sort((left, right) => right.created_at.localeCompare(left.created_at));
    });
  }, []);

  const handleAuthExpired = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
    void clearSession().finally(onUnpair);
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

  const refreshApprovals = useCallback(async () => {
    const pending = await listPendingApprovals(session);
    mergePendingApprovals(pending);
  }, [mergePendingApprovals, session]);

  useEffect(() => {
    void requestNotificationPermission()
      .then((allowed) => setNotificationsOff(!allowed))
      .catch(() => setNotificationsOff(true));
  }, []);

  useEffect(() => {
    let closedByEffect = false;
    setConnection("connecting");
    setError("");
    void refreshApprovals().catch((currentError: unknown) => {
      if (currentError instanceof AuthExpiredError) {
        handleAuthExpired();
        return;
      }
      setError(errorMessage(currentError));
    });

    const socket = new WebSocket(approvalWebSocketUrl(session), mobileAuthWebSocketProtocols(session));
    socketRef.current = socket;

    socket.onopen = () => {
      setConnection("online");
      setError("");
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as ApprovalEvent;
        if (payload.type === "connected") {
          mergePendingApprovals(payload.pending);
          return;
        }
        if (payload.type === "approval_notification" || payload.type === "approval_created") {
          upsertApproval(payload.approval);
          void notifyApproval(payload.approval);
          return;
        }
        if (payload.type === "approval_decided") {
          upsertApproval(payload.approval);
        }
      } catch {
        // Polling remains available if a stream event is malformed.
      }
    };

    socket.onerror = () => {
      setError("无法保持与电脑的连接。请确认 Mavris 已打开，然后点刷新。");
    };

    socket.onclose = (event) => {
      if (event.code === 1008) {
        handleAuthExpired();
        return;
      }
      if (!closedByEffect) setConnection("offline");
    };

    return () => {
      closedByEffect = true;
      if (socketRef.current === socket) socketRef.current = null;
      socket.close();
    };
  }, [handleAuthExpired, mergePendingApprovals, refreshApprovals, session, streamReconnectKey, upsertApproval]);

  const disconnectPhone = async () => {
    socketRef.current?.close();
    socketRef.current = null;
    await clearSession();
    onUnpair();
  };

  const handleUnpair = () => {
    Alert.alert("断开手机连接？", "之后仍可在电脑端 Mavris 重新连接。", [
      { text: "取消", style: "cancel" },
      { text: "断开连接", onPress: () => void disconnectPhone(), style: "destructive" },
    ]);
  };

  const handleRefresh = () => {
    setStreamReconnectKey((current) => current + 1);
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor="#f6f4ee" />
      <View style={styles.header}>
        <View>
          <Text style={styles.kicker}>{connection === "online" ? "已连接电脑" : "正在连接"}</Text>
          <Text style={styles.headerTitle}>{headerTitle}</Text>
        </View>
        <View style={styles.headerActions}>
          <IconButton accessibilityLabel="刷新请求" icon={<RefreshCcw size={18} color="#23313d" />} onPress={handleRefresh} />
          <IconButton accessibilityLabel="断开手机连接" icon={<Unlink size={18} color="#8c2f39" />} onPress={handleUnpair} />
        </View>
      </View>

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

      <FlatList
        contentContainerStyle={approvals.length ? styles.list : styles.emptyList}
        data={approvals}
        keyExtractor={(approval) => approval.id}
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <ShieldCheck size={34} color="#5f6b76" />
            <Text style={styles.emptyTitle}>暂无待处理</Text>
            <Text style={styles.emptyText}>电脑端的新审批请求会显示在这里。</Text>
          </View>
        }
        renderItem={({ item }) => <ApprovalCard approval={item} onPress={() => onSelectApproval(item)} />}
      />
    </SafeAreaView>
  );
}

function ApprovalCard({ approval, onPress }: { approval: BackendApproval; onPress: () => void }) {
  const pending = approval.status === "pending";
  const preview = readablePreview(approval.diff_preview);
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.card, pressed && styles.pressed]}>
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleWrap}>
          <Text style={styles.cardTitle}>{approvalCardTitle(approval)}</Text>
          <Text style={styles.cardMeta}>{shortDate(approval.created_at)}</Text>
        </View>
        <View style={styles.cardStatus}>
          <Text style={[styles.badge, pending ? styles.badgePending : styles.badgeDone]}>{approvalStatusText(approval)}</Text>
          <ChevronRight size={18} color="#65717c" />
        </View>
      </View>
      <Text style={styles.message}>{approval.message}</Text>
      <Text numberOfLines={3} style={styles.preview}>{preview}</Text>
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
      onPress={onPress}
      style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
    >
      {icon}
    </Pressable>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.includes("Failed to fetch")) {
    return "无法连接到电脑。请确认 Mavris 已打开，然后点刷新。";
  }
  return "无法更新请求。请点刷新重试。";
}

function connectionStatusText(connection: ApprovalConnection): string {
  if (connection === "online") return "已连接";
  if (connection === "connecting") return "正在连接";
  return "离线，请点刷新重试";
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
  const preview = formatPreview(value);
  if (preview === "暂无预览内容") return "打开后查看详情。";
  if (preview.trim().startsWith("{") || preview.trim().startsWith("[")) return "打开后查看详情。";
  return preview;
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f6f4ee",
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
    width: 42,
    height: 42,
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
  list: {
    padding: 20,
    gap: 14,
  },
  emptyList: {
    flexGrow: 1,
    padding: 20,
  },
  listFooter: {
    marginTop: 14,
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
  card: {
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
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  badge: {
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
  message: {
    color: "#27343f",
    lineHeight: 22,
    fontSize: 15,
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
