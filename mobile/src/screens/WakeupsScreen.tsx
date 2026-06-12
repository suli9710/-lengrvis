import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Platform,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { AlarmClock, ArrowLeft, Check, RefreshCcw, X } from "lucide-react-native";

import {
  AuthExpiredError,
  BackendHttpError,
  approveMobileWakeup,
  listPendingMobileWakeups,
  rejectMobileWakeup,
  type BackendWakeup,
  type PairingSession,
} from "../api/client";
import { shortDate } from "../format";
import { safeDisplayText } from "../safeDisplay";

export function WakeupsScreen({
  session,
  onBack,
  onSessionExpired,
}: {
  session: PairingSession;
  onBack: () => void;
  onSessionExpired: () => void;
}) {
  const [wakeups, setWakeups] = useState<BackendWakeup[]>([]);
  const [error, setError] = useState("");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [decisionId, setDecisionId] = useState("");

  const pendingCount = useMemo(
    () => wakeups.filter((wakeup) => wakeup.status === "pending").length,
    [wakeups],
  );
  const headerTitle = pendingCount === 0 ? "暂无定时唤醒" : `${pendingCount} 项待确认`;
  const isInitialLoading = !hasLoadedOnce && wakeups.length === 0;

  const refreshWakeups = useCallback(async () => {
    const pending = await listPendingMobileWakeups(session);
    setWakeups(pending);
  }, [session]);

  useEffect(() => {
    setError("");
    void refreshWakeups()
      .catch((currentError: unknown) => {
        if (currentError instanceof AuthExpiredError) {
          onSessionExpired();
          return;
        }
        setError(errorMessage(currentError));
      })
      .finally(() => setHasLoadedOnce(true));
  }, [onSessionExpired, refreshWakeups]);

  const handleRefresh = () => {
    if (isRefreshing) return;
    setIsRefreshing(true);
    setError("");
    void refreshWakeups()
      .catch((currentError: unknown) => {
        if (currentError instanceof AuthExpiredError) {
          onSessionExpired();
          return;
        }
        setError(errorMessage(currentError));
      })
      .finally(() => {
        setHasLoadedOnce(true);
        setIsRefreshing(false);
      });
  };

  const submitDecision = async (wakeup: BackendWakeup, decision: "approve" | "reject") => {
    if (decisionId) return;
    setDecisionId(`${wakeup.id}:${decision}`);
    setError("");
    try {
      const updated =
        decision === "approve"
          ? await approveMobileWakeup(session, wakeup.id)
          : await rejectMobileWakeup(session, wakeup.id);
      setWakeups((current) => current.filter((item) => item.id !== wakeup.id).concat(updated.status === "pending" ? [updated] : []));
      if (updated.status === "pending") {
        await refreshWakeups();
      }
    } catch (currentError) {
      if (currentError instanceof AuthExpiredError) {
        onSessionExpired();
        return;
      }
      setError(errorMessage(currentError));
      void refreshWakeups().catch(() => undefined);
    } finally {
      setDecisionId("");
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor="#f6f4ee" />
      <View style={styles.header}>
        <Pressable
          accessibilityLabel="返回审批列表"
          accessibilityRole="button"
          hitSlop={4}
          onPress={onBack}
          style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}
        >
          <ArrowLeft size={18} color="#23313d" />
        </Pressable>
        <View style={styles.headerCopy}>
          <Text style={styles.kicker}>定时唤醒</Text>
          <Text style={styles.headerTitle}>{headerTitle}</Text>
        </View>
        <Pressable
          accessibilityLabel="刷新唤醒列表"
          accessibilityRole="button"
          hitSlop={4}
          onPress={handleRefresh}
          style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
        >
          <RefreshCcw size={18} color="#23313d" />
        </Pressable>
      </View>

      {error ? <Text style={styles.errorBanner}>{error}</Text> : null}

      <FlatList
        contentContainerStyle={wakeups.length ? styles.list : styles.emptyList}
        data={wakeups}
        keyExtractor={(wakeup) => wakeup.id}
        ListEmptyComponent={
          isInitialLoading ? (
            <View accessible accessibilityLabel="正在加载定时唤醒" style={styles.emptyState}>
              <ActivityIndicator color="#0e5f76" />
              <Text style={styles.emptyTitle}>正在同步</Text>
              <Text style={styles.emptyText}>手机正在向电脑端加载待确认的定时唤醒。</Text>
            </View>
          ) : (
            <View style={styles.emptyState}>
              <AlarmClock size={34} color="#5f6b76" />
              <Text style={styles.emptyTitle}>暂无定时唤醒</Text>
              <Text style={styles.emptyText}>电脑端有新的定时任务唤醒时会显示在这里。</Text>
            </View>
          )
        }
        onRefresh={handleRefresh}
        refreshing={isRefreshing}
        renderItem={({ item }) => (
          <WakeupCard
            busy={decisionId.startsWith(`${item.id}:`)}
            onApprove={() => void submitDecision(item, "approve")}
            onReject={() => void submitDecision(item, "reject")}
            wakeup={item}
          />
        )}
        style={styles.listViewport}
      />
    </SafeAreaView>
  );
}

function WakeupCard({
  wakeup,
  busy,
  onApprove,
  onReject,
}: {
  wakeup: BackendWakeup;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const title = wakeupDisplayTitle(wakeup);
  const detail = safeDisplayText(wakeup.body || wakeup.goal, "打开后查看唤醒详情。");
  const pending = wakeup.status === "pending";
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleWrap}>
          <Text style={styles.cardTitle}>{title}</Text>
          <Text style={styles.cardMeta}>{shortDate(wakeup.due_at || wakeup.created_at)}</Text>
        </View>
        <Text style={[styles.badge, pending ? styles.badgePending : styles.badgeDone]}>{wakeupStatusLabel(wakeup.status)}</Text>
      </View>
      <Text style={styles.message}>{detail}</Text>
      {pending ? (
        <View style={styles.actions}>
          <Pressable
            accessibilityLabel="拒绝定时唤醒"
            accessibilityRole="button"
            accessibilityState={{ busy, disabled: busy }}
            disabled={busy}
            hitSlop={4}
            onPress={onReject}
            style={({ pressed }) => [styles.actionButton, styles.rejectButton, busy && styles.disabledAction, pressed && styles.pressed]}
          >
            <X size={15} color="#8c2f39" />
            <Text style={styles.rejectText}>拒绝</Text>
          </Pressable>
          <Pressable
            accessibilityLabel="批准定时唤醒"
            accessibilityRole="button"
            accessibilityState={{ busy, disabled: busy }}
            disabled={busy}
            hitSlop={4}
            onPress={onApprove}
            style={({ pressed }) => [styles.actionButton, styles.approveButton, busy && styles.disabledAction, pressed && styles.pressed]}
          >
            <Check size={15} color="#ffffff" />
            <Text style={styles.approveText}>批准</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

function wakeupDisplayTitle(wakeup: BackendWakeup): string {
  const title = safeDisplayText(wakeup.title, "");
  if (title) return title;
  const goal = safeDisplayText(wakeup.goal, "");
  if (goal) return goal.length > 48 ? `${goal.slice(0, 48)}…` : goal;
  return "定时唤醒";
}

function wakeupStatusLabel(status: BackendWakeup["status"]): string {
  if (status === "pending") return "待确认";
  if (status === "approved") return "已批准";
  if (status === "rejected") return "已拒绝";
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  return status;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && (error.name === "FetchTimeoutError" || error.message.includes("timed out"))) {
    return error.message;
  }
  if (error instanceof Error && error.message.includes("Failed to fetch")) {
    return "无法连接到电脑。请确认 Lengrvis 已打开，然后点刷新。";
  }
  if (error instanceof BackendHttpError && error.status === 409) {
    return error.detail || "这项唤醒已被处理。请刷新列表。";
  }
  if (error instanceof BackendHttpError && error.status >= 500) {
    return "电脑端暂时无法处理请求。请稍后刷新重试。";
  }
  return "无法更新唤醒列表。请点刷新重试。";
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
    alignItems: "center",
    gap: 10,
  },
  backButton: {
    width: 48,
    height: 48,
    borderRadius: 8,
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "#d7dedf",
  },
  headerCopy: {
    flex: 1,
    minWidth: 0,
  },
  kicker: {
    color: "#65717c",
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  headerTitle: {
    color: "#1f2933",
    fontSize: 24,
    fontWeight: "800",
    marginTop: 2,
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
  errorBanner: {
    marginHorizontal: 20,
    marginBottom: 8,
    color: "#8c2f39",
    lineHeight: 20,
  },
  list: {
    paddingBottom: Platform.select({ android: 96, default: 28 }),
    gap: 14,
  },
  emptyList: {
    flexGrow: 1,
    paddingBottom: Platform.select({ android: 96, default: 28 }),
  },
  listViewport: {
    flex: 1,
  },
  emptyState: {
    alignItems: "center",
    gap: 10,
    paddingTop: 90,
    paddingHorizontal: 24,
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
  actions: {
    flexDirection: "row",
    gap: 10,
  },
  actionButton: {
    flex: 1,
    minHeight: 48,
    borderRadius: 8,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  rejectButton: {
    borderWidth: 1,
    borderColor: "#e1b8be",
    backgroundColor: "#fff5f6",
  },
  approveButton: {
    backgroundColor: "#1f7a4d",
  },
  rejectText: {
    color: "#8c2f39",
    fontSize: 13,
    fontWeight: "900",
  },
  approveText: {
    color: "#ffffff",
    fontSize: 13,
    fontWeight: "900",
  },
  disabledAction: {
    opacity: 0.44,
  },
  pressed: {
    opacity: 0.72,
  },
});
