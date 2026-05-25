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
import { ChevronRight, RefreshCcw, ShieldCheck, Unlink } from "lucide-react-native";
import type { ReactNode } from "react";

import {
  approvalWebSocketUrl,
  listPendingApprovals,
  type ApprovalEvent,
  type BackendApproval,
  type PairingSession,
} from "../api/client";
import { approvalStatusLabel, approvalTitle, formatPreview, shortDate } from "../format";
import { notifyApproval, requestNotificationPermission } from "../notifications";
import { clearSession } from "../store/auth";

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
  const [connection, setConnection] = useState<"offline" | "connecting" | "online">("offline");
  const [error, setError] = useState("");
  const socketRef = useRef<WebSocket | null>(null);

  const pendingCount = useMemo(
    () => approvals.filter((approval) => approval.status === "pending").length,
    [approvals],
  );

  const upsertApproval = useCallback((approval: BackendApproval) => {
    setApprovals((current) => {
      const next = current.filter((item) => item.id !== approval.id);
      return [approval, ...next].sort((left, right) => right.created_at.localeCompare(left.created_at));
    });
  }, []);

  const refreshApprovals = useCallback(async () => {
    const pending = await listPendingApprovals(session);
    setApprovals(pending);
  }, [session]);

  useEffect(() => {
    void requestNotificationPermission();
  }, []);

  useEffect(() => {
    let closedByEffect = false;
    setConnection("connecting");
    void refreshApprovals().catch((currentError: unknown) => setError(errorMessage(currentError)));

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
          setApprovals(payload.pending);
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
      setError("WebSocket connection failed. Check LAN address and backend port.");
    };

    socket.onclose = () => {
      if (!closedByEffect) setConnection("offline");
    };

    return () => {
      closedByEffect = true;
      socket.close();
    };
  }, [refreshApprovals, session, upsertApproval]);

  const handleUnpair = async () => {
    socketRef.current?.close();
    socketRef.current = null;
    await clearSession();
    onUnpair();
  };

  const handleRefresh = () => {
    void refreshApprovals().catch((currentError: unknown) => Alert.alert("Refresh failed", errorMessage(currentError)));
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor="#f6f4ee" />
      <View style={styles.header}>
        <View>
          <Text style={styles.kicker}>{connection === "online" ? "Live approval stream" : "Approval companion"}</Text>
          <Text style={styles.headerTitle}>{pendingCount} pending</Text>
        </View>
        <View style={styles.headerActions}>
          <IconButton icon={<RefreshCcw size={18} color="#23313d" />} onPress={handleRefresh} />
          <IconButton icon={<Unlink size={18} color="#8c2f39" />} onPress={() => void handleUnpair()} />
        </View>
      </View>

      <View style={styles.statusRow}>
        <ShieldCheck size={16} color={connection === "online" ? "#1f7a4d" : "#a46a00"} />
        <Text style={styles.statusText}>{connection === "online" ? "Connected with JWT" : "Waiting for WebSocket"}</Text>
      </View>
      {error ? <Text style={styles.errorBanner}>{error}</Text> : null}

      <FlatList
        contentContainerStyle={approvals.length ? styles.list : styles.emptyList}
        data={approvals}
        keyExtractor={(approval) => approval.id}
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <ShieldCheck size={34} color="#5f6b76" />
            <Text style={styles.emptyTitle}>No approvals waiting</Text>
            <Text style={styles.emptyText}>New approval gates will appear here and trigger a local notification.</Text>
          </View>
        }
        renderItem={({ item }) => <ApprovalCard approval={item} onPress={() => onSelectApproval(item)} />}
      />
    </SafeAreaView>
  );
}

function ApprovalCard({ approval, onPress }: { approval: BackendApproval; onPress: () => void }) {
  const pending = approval.status === "pending";
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.card, pressed && styles.pressed]}>
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleWrap}>
          <Text style={styles.cardTitle}>{approvalTitle(approval)}</Text>
          <Text style={styles.cardMeta}>{shortDate(approval.created_at)}</Text>
        </View>
        <View style={styles.cardStatus}>
          <Text style={[styles.badge, pending ? styles.badgePending : styles.badgeDone]}>{approvalStatusLabel(approval.status)}</Text>
          <ChevronRight size={18} color="#65717c" />
        </View>
      </View>
      <Text style={styles.message}>{approval.message}</Text>
      <Text numberOfLines={4} style={styles.preview}>{formatPreview(approval.diff_preview)}</Text>
    </Pressable>
  );
}

function IconButton({ icon, onPress }: { icon: ReactNode; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}>
      {icon}
    </Pressable>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed";
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
    justifyContent: "center",
    padding: 24,
  },
  emptyState: {
    alignItems: "center",
    gap: 10,
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
