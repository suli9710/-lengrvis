import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
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
import * as Device from "expo-device";
import { Check, Link2, RefreshCcw, ShieldCheck, Smartphone, Unlink, X } from "lucide-react-native";
import type { ReactNode } from "react";

import {
  approvalWebSocketUrl,
  listPendingApprovals,
  pairWithBackend,
  submitApprovalDecision,
  type ApprovalEvent,
  type BackendApproval,
  type PairingSession,
} from "./src/api";
import { approvalStatusLabel, approvalTitle, formatPreview, shortDate } from "./src/format";
import { notifyApproval, requestNotificationPermission } from "./src/notifications";
import { clearSession, loadSession, saveSession } from "./src/storage";

const defaultBaseUrl = "http://127.0.0.1:8000";

export default function App() {
  const [session, setSession] = useState<PairingSession | null>(null);
  const [baseUrl, setBaseUrl] = useState(defaultBaseUrl);
  const [pairCode, setPairCode] = useState("");
  const [approvals, setApprovals] = useState<BackendApproval[]>([]);
  const [connection, setConnection] = useState<"offline" | "connecting" | "online">("offline");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState("");
  const socketRef = useRef<WebSocket | null>(null);

  const pendingCount = useMemo(
    () => approvals.filter((approval) => approval.status === "pending").length,
    [approvals],
  );

  const refreshApprovals = useCallback(async () => {
    if (!session) return;
    const pending = await listPendingApprovals(session);
    setApprovals(pending);
  }, [session]);

  useEffect(() => {
    void requestNotificationPermission();
    void loadSession().then((stored) => {
      if (!stored) return;
      setSession(stored);
      setBaseUrl(stored.baseUrl);
    });
  }, []);

  useEffect(() => {
    if (!session) {
      socketRef.current?.close();
      setConnection("offline");
      return;
    }

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
        if (payload.type === "approval_created") {
          upsertApproval(payload.approval);
          void notifyApproval(payload.approval);
          return;
        }
        if (payload.type === "approval_decided") {
          upsertApproval(payload.approval);
        }
      } catch {
        // Ignore malformed stream events; refresh remains available.
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
  }, [refreshApprovals, session]);

  const handlePair = async () => {
    const code = pairCode.replace(/\D/g, "");
    if (code.length !== 6) {
      Alert.alert("Pairing code", "Enter the 6 digit code from Mavris desktop.");
      return;
    }
    setIsBusy(true);
    setError("");
    try {
      const nextSession = await pairWithBackend(baseUrl, code, Device.deviceName ?? "Android device");
      await saveSession(nextSession);
      setSession(nextSession);
      setPairCode("");
    } catch (currentError) {
      setError(errorMessage(currentError));
    } finally {
      setIsBusy(false);
    }
  };

  const handleDecision = async (approval: BackendApproval, decision: "approved" | "denied") => {
    if (!session) return;
    setIsBusy(true);
    try {
      const updated = await submitApprovalDecision(session, approval.id, decision);
      upsertApproval(updated);
    } catch (currentError) {
      Alert.alert("Decision failed", errorMessage(currentError));
    } finally {
      setIsBusy(false);
    }
  };

  const handleUnpair = async () => {
    socketRef.current?.close();
    socketRef.current = null;
    await clearSession();
    setSession(null);
    setApprovals([]);
    setPairCode("");
  };

  const upsertApproval = (approval: BackendApproval) => {
    setApprovals((current) => {
      const next = current.filter((item) => item.id !== approval.id);
      return [approval, ...next].sort((left, right) => right.created_at.localeCompare(left.created_at));
    });
  };

  if (!session) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar barStyle="dark-content" backgroundColor="#f6f4ee" />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.centerScreen}>
          <View style={styles.pairIcon}>
            <Smartphone size={34} color="#1f2933" />
          </View>
          <Text style={styles.title}>Mavris Approval</Text>
          <Text style={styles.subtitle}>Pair on the same LAN to approve desktop tasks from Android.</Text>

          <View style={styles.form}>
            <Text style={styles.label}>Backend URL</Text>
            <TextInput
              autoCapitalize="none"
              autoCorrect={false}
              inputMode="url"
              onChangeText={setBaseUrl}
              placeholder="http://192.168.1.20:8000"
              style={styles.input}
              value={baseUrl}
            />
            <Text style={styles.label}>Pairing Code</Text>
            <TextInput
              keyboardType="number-pad"
              maxLength={6}
              onChangeText={setPairCode}
              placeholder="6 digits"
              style={[styles.input, styles.codeInput]}
              value={pairCode}
            />
            {error ? <Text style={styles.errorText}>{error}</Text> : null}
            <Pressable disabled={isBusy} onPress={handlePair} style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed]}>
              {isBusy ? <ActivityIndicator color="#ffffff" /> : <Link2 size={18} color="#ffffff" />}
              <Text style={styles.primaryButtonText}>Pair Device</Text>
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor="#f6f4ee" />
      <View style={styles.header}>
        <View>
          <Text style={styles.kicker}>{connection === "online" ? "Live approval stream" : "Approval companion"}</Text>
          <Text style={styles.headerTitle}>{pendingCount} pending</Text>
        </View>
        <View style={styles.headerActions}>
          <IconButton icon={<RefreshCcw size={18} color="#23313d" />} onPress={() => void refreshApprovals()} />
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
        renderItem={({ item }) => (
          <ApprovalCard approval={item} busy={isBusy} onDecision={handleDecision} />
        )}
      />
    </SafeAreaView>
  );
}

function ApprovalCard({
  approval,
  busy,
  onDecision,
}: {
  approval: BackendApproval;
  busy: boolean;
  onDecision: (approval: BackendApproval, decision: "approved" | "denied") => void;
}) {
  const pending = approval.status === "pending";
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <View>
          <Text style={styles.cardTitle}>{approvalTitle(approval)}</Text>
          <Text style={styles.cardMeta}>{shortDate(approval.created_at)}</Text>
        </View>
        <Text style={[styles.badge, pending ? styles.badgePending : styles.badgeDone]}>{approvalStatusLabel(approval.status)}</Text>
      </View>
      <Text style={styles.message}>{approval.message}</Text>
      <Text style={styles.preview}>{formatPreview(approval.diff_preview)}</Text>
      {pending ? (
        <View style={styles.decisionRow}>
          <Pressable disabled={busy} onPress={() => onDecision(approval, "denied")} style={({ pressed }) => [styles.denyButton, pressed && styles.pressed]}>
            <X size={18} color="#8c2f39" />
            <Text style={styles.denyText}>Deny</Text>
          </Pressable>
          <Pressable disabled={busy} onPress={() => onDecision(approval, "approved")} style={({ pressed }) => [styles.approveButton, pressed && styles.pressed]}>
            <Check size={18} color="#ffffff" />
            <Text style={styles.approveText}>Approve</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
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
  centerScreen: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
  },
  pairIcon: {
    width: 68,
    height: 68,
    borderRadius: 18,
    backgroundColor: "#e7ece8",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 22,
  },
  title: {
    color: "#1f2933",
    fontSize: 31,
    fontWeight: "800",
  },
  subtitle: {
    color: "#5f6b76",
    fontSize: 16,
    lineHeight: 23,
    marginTop: 8,
  },
  form: {
    marginTop: 30,
    gap: 10,
  },
  label: {
    color: "#3a4651",
    fontSize: 13,
    fontWeight: "700",
  },
  input: {
    minHeight: 52,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#cbd4d9",
    backgroundColor: "#ffffff",
    color: "#1f2933",
    fontSize: 16,
    paddingHorizontal: 14,
  },
  codeInput: {
    fontSize: 24,
    fontWeight: "800",
    letterSpacing: 0,
    textAlign: "center",
  },
  primaryButton: {
    minHeight: 52,
    borderRadius: 8,
    backgroundColor: "#0e5f76",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 9,
    marginTop: 8,
  },
  primaryButtonText: {
    color: "#ffffff",
    fontSize: 16,
    fontWeight: "800",
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
  errorText: {
    color: "#8c2f39",
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
  cardTitle: {
    color: "#1f2933",
    fontSize: 18,
    fontWeight: "800",
  },
  cardMeta: {
    color: "#65717c",
    marginTop: 3,
  },
  badge: {
    borderRadius: 999,
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
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: undefined }),
  },
  decisionRow: {
    flexDirection: "row",
    gap: 10,
  },
  denyButton: {
    flex: 1,
    minHeight: 46,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#e1b8be",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
  },
  approveButton: {
    flex: 1,
    minHeight: 46,
    borderRadius: 8,
    backgroundColor: "#1f7a4d",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
  },
  denyText: {
    color: "#8c2f39",
    fontWeight: "800",
  },
  approveText: {
    color: "#ffffff",
    fontWeight: "800",
  },
  pressed: {
    opacity: 0.72,
  },
});
