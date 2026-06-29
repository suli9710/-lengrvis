import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { AlarmClock, Check, RefreshCcw, X } from "lucide-react-native";

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
import { ActionButton, EmptyState, IconButton, NoticeBanner, ScreenShell, StatusPill, TopBar } from "../ui/Primitives";
import { colors, radii } from "../ui/theme";

export function WakeupsScreen({
  session,
  onBack: _onBack,
  onSessionExpired,
}: {
  session: PairingSession;
  onBack: () => void;
  onSessionExpired: () => void;
}) {
  void _onBack;
  const [wakeups, setWakeups] = useState<BackendWakeup[]>([]);
  const [error, setError] = useState("");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [decisionId, setDecisionId] = useState("");
  const decisionLockRef = useRef(false);

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
    if (decisionLockRef.current) return;
    decisionLockRef.current = true;
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
      decisionLockRef.current = false;
      setDecisionId("");
    }
  };

  return (
    <ScreenShell scroll={false} testID="wakeups-screen">
      <TopBar
        action={<IconButton accessibilityLabel="刷新唤醒列表" icon={<RefreshCcw size={18} color={colors.ink} />} onPress={handleRefresh} />}
        detail="电脑端计划在未来唤醒任务时，会先让手机确认。"
        kicker="定时唤醒"
        title={headerTitle}
      />

      {error ? <NoticeBanner detail={error} title="同步失败" tone="danger" /> : null}

      <FlatList
        contentContainerStyle={wakeups.length ? styles.list : styles.emptyList}
        data={wakeups}
        keyExtractor={(wakeup) => wakeup.id}
        ListEmptyComponent={
          isInitialLoading ? (
            <EmptyState
              icon={<ActivityIndicator color={colors.accent} />}
              title="正在同步"
              detail="手机正在向电脑端加载待确认的定时唤醒。"
            />
          ) : (
            <EmptyState
              icon={<AlarmClock size={34} color={colors.inkSubtle} />}
              title="暂无定时唤醒"
              detail="电脑端有新的定时任务唤醒时会显示在这里。"
            />
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
      />
    </ScreenShell>
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
        <StatusPill label={wakeupStatusLabel(wakeup.status)} tone={pending ? "warning" : "success"} />
      </View>
      <Text style={styles.message}>{detail}</Text>
      {pending ? (
        <View style={styles.actions}>
          <ActionButton disabled={busy} icon={<X size={15} color={colors.danger} />} label="拒绝" onPress={onReject} tone="neutral" />
          <ActionButton disabled={busy} icon={<Check size={15} color="#ffffff" />} label="批准" onPress={onApprove} tone="success" />
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
  list: {
    paddingBottom: Platform.select({ android: 118, default: 110 }),
    gap: 12,
  },
  emptyList: {
    flexGrow: 1,
    paddingBottom: Platform.select({ android: 118, default: 110 }),
  },
  card: {
    borderRadius: radii.md,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
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
    color: colors.ink,
    fontSize: 18,
    fontWeight: "900",
  },
  cardMeta: {
    color: colors.inkSubtle,
    marginTop: 3,
  },
  message: {
    color: colors.inkMuted,
    lineHeight: 22,
    fontSize: 15,
  },
  actions: {
    flexDirection: "row",
    gap: 10,
  },
  pressed: {
    opacity: 0.72,
  },
});
