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
  const headerTitle = pendingCount === 0 ? "All caught up" : `${pendingCount} waiting`;

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

    const socket = new WebSocket(approvalWebSocketUrl(session));
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
      setError("Can't stay connected to your computer. Make sure Mavris is open, then tap refresh.");
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
    Alert.alert("Disconnect phone?", "You can connect again later from Mavris on your computer.", [
      { text: "Cancel", style: "cancel" },
      { text: "Disconnect", onPress: () => void disconnectPhone(), style: "destructive" },
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
          <Text style={styles.kicker}>{connection === "online" ? "Connected to computer" : "Trying to connect"}</Text>
          <Text style={styles.headerTitle}>{headerTitle}</Text>
        </View>
        <View style={styles.headerActions}>
          <IconButton accessibilityLabel="Refresh requests" icon={<RefreshCcw size={18} color="#23313d" />} onPress={handleRefresh} />
          <IconButton accessibilityLabel="Disconnect phone" icon={<Unlink size={18} color="#8c2f39" />} onPress={handleUnpair} />
        </View>
      </View>

      <View style={styles.statusRow}>
        <ShieldCheck size={16} color={connection === "online" ? "#1f7a4d" : "#a46a00"} />
        <Text style={styles.statusText}>{connectionStatusText(connection)}</Text>
      </View>
      {notificationsOff ? (
        <View style={styles.noticeRow}>
          <BellOff size={16} color="#7a5700" />
          <Text style={styles.noticeText}>Phone alerts are off. Leave this open or tap refresh to check for requests.</Text>
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
            <Text style={styles.emptyTitle}>You're all caught up</Text>
            <Text style={styles.emptyText}>New requests from your computer will appear here.</Text>
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
    return "Could not reach your computer. Make sure Mavris is open, then tap refresh.";
  }
  return "Could not update requests. Tap refresh to try again.";
}

function connectionStatusText(connection: ApprovalConnection): string {
  if (connection === "online") return "Connected";
  if (connection === "connecting") return "Connecting";
  return "Offline - tap refresh to try again";
}

function approvalStatusText(approval: BackendApproval): string {
  if (approval.status === "pending") return "Waiting";
  return approvalStatusLabel(approval.status);
}

function approvalCardTitle(approval: BackendApproval): string {
  if (approval.approval_type === "tool_call") return "Review request";
  return approvalTitle(approval).replace("Tool Approval", "Review request").replace("Tool approval", "Review request");
}

function readablePreview(value: unknown): string {
  const preview = formatPreview(value);
  if (preview === "No preview payload") return "Open to review the details.";
  if (preview.trim().startsWith("{") || preview.trim().startsWith("[")) return "Open to review the details.";
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
