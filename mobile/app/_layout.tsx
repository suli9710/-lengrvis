import { Slot, usePathname, useRouter } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, BackHandler, Platform, Pressable, SafeAreaView, StatusBar, StyleSheet, Text, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";

import { AuthExpiredError, clearRemoteInputGrantTokens, getApprovalDetail, isExpiredTimestamp, refreshMobileSession, type PairingSession, type RemoteInputGrant } from "../src/api/client";
import { resolveAndroidBack } from "../src/androidBackNavigation";
import { addApprovalNotificationResponseListener, getLastApprovalNotificationApprovalId } from "../src/notifications";
import { reduceRemoteInputGrant, remoteInputGrantExpiryDelayMs, isRemoteInputGrantUsable } from "../src/remoteInputGrant";
import { ConsentScreen } from "../src/screens/ConsentScreen";
import { PairScreen } from "../src/screens/PairScreen";
import { MobileCompanionProvider, useMobileCompanion } from "../src/state/MobileCompanionContext";
import { sessionRefreshDelayMs, sessionRefreshRetryDelayMs } from "../src/sessionLifecycle";
import { clearSession, loadSession, replaceSessionIfTokenMatches } from "../src/store/auth";
import { loadConsentState } from "../src/store/consent";
import { colors } from "../src/ui/theme";

type SessionLoadState = "loading" | "ready" | "failed";
type ConsentGateState = "checking" | "needed" | "done";

export default function RootLayout() {
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<PairingSession | null>(null);
  const [sessionLoadState, setSessionLoadState] = useState<SessionLoadState>("loading");
  const [sessionLoadAttempt, setSessionLoadAttempt] = useState(0);
  const [sessionRefreshAttempt, setSessionRefreshAttempt] = useState(0);
  const [remoteInputGrant, setRemoteInputGrant] = useState<RemoteInputGrant | null>(null);
  const [consentGate, setConsentGate] = useState<ConsentGateState>("checking");

  useEffect(() => {
    let isActive = true;
    void loadConsentState()
      .then((state) => {
        if (!isActive) return;
        setConsentGate(state.needsConsent ? "needed" : "done");
      })
      .catch(() => {
        if (!isActive) return;
        setConsentGate("needed");
      });
    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    if (consentGate !== "done") return undefined;
    let isActive = true;
    setSessionLoadState("loading");
    void loadSession()
      .then(async (storedSession) => {
        if (!isActive) return;
        let stored = storedSession;
        if (stored && isExpiredTimestamp(stored.expiresAt)) {
          const refreshed = await refreshMobileSession(stored);
          const replaced = await replaceSessionIfTokenMatches(stored.token, refreshed);
          if (!replaced || !isActive) return;
          stored = refreshed;
        }
        if (!stored) {
          setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "cleared" }));
          router.replace("/");
        }
        setSession(stored);
        setSessionLoadState("ready");
      })
      .catch(() => {
        if (!isActive) return;
        setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "cleared" }));
        setSession(null);
        setSessionLoadState("failed");
        router.replace("/");
      });
    return () => {
      isActive = false;
    };
  }, [consentGate, router, sessionLoadAttempt]);

  useEffect(() => {
    if (!remoteInputGrant) return undefined;
    if (!isRemoteInputGrantUsable(remoteInputGrant)) {
      clearRemoteInputGrantTokens();
      setRemoteInputGrant(null);
      return undefined;
    }
    const remainingMs = remoteInputGrantExpiryDelayMs(remoteInputGrant);
    if (remainingMs === null) {
      clearRemoteInputGrantTokens();
      setRemoteInputGrant(null);
      return undefined;
    }
    const grantId = remoteInputGrant.id;
    const timeout = setTimeout(() => {
      clearRemoteInputGrantTokens();
      setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "expired", grantId }));
    }, remainingMs);
    return () => clearTimeout(timeout);
  }, [remoteInputGrant]);

  const resetShellState = useCallback(() => {
    clearRemoteInputGrantTokens();
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "cleared" }));
    router.replace("/home");
  }, [router]);

  const clearLocalSessionOrShowRecovery = useCallback(() => {
    resetShellState();
    setSession(null);
    setSessionLoadState("loading");
    void clearSession()
      .then(() => {
        setSessionLoadState("ready");
        router.replace("/");
      })
      .catch(() => {
        setSessionLoadState("failed");
        router.replace("/");
      });
  }, [resetShellState, router]);

  useEffect(() => {
    if (!session) return undefined;
    const baseSession = session;
    const refreshDelay = sessionRefreshDelayMs(baseSession);
    if (refreshDelay === null) return undefined;
    let active = true;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    const refreshTimer = setTimeout(() => {
      void refreshMobileSession(baseSession)
        .then(async (nextSession) => {
          const replaced = await replaceSessionIfTokenMatches(baseSession.token, nextSession);
          if (!active || !replaced) return;
          setSessionRefreshAttempt(0);
          setSession((current) => current?.token === baseSession.token ? nextSession : current);
        })
        .catch((error: unknown) => {
          if (!active) return;
          if (error instanceof AuthExpiredError) {
            clearLocalSessionOrShowRecovery();
            return;
          }
          retryTimer = setTimeout(
            () => setSessionRefreshAttempt((attempt) => attempt + 1),
            sessionRefreshRetryDelayMs(sessionRefreshAttempt),
          );
        });
    }, refreshDelay);
    return () => {
      active = false;
      clearTimeout(refreshTimer);
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [clearLocalSessionOrShowRecovery, session, sessionRefreshAttempt]);

  const handlePaired = useCallback((nextSession: PairingSession) => {
    resetShellState();
    setSessionLoadState("ready");
    setSession(nextSession);
    router.replace("/home");
  }, [resetShellState, router]);

  const handleRemoteInputGrant = useCallback((grant: RemoteInputGrant) => {
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "received", grant }));
  }, []);

  const handleRemoteInputGrantRevoked = useCallback((grant: RemoteInputGrant) => {
    clearRemoteInputGrantTokens();
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "revoked", grantId: grant.id }));
  }, []);

  const routeState = useMemo(() => routeStateFromPath(pathname), [pathname]);

  const handleAndroidBack = useCallback((): boolean => {
    const action = resolveAndroidBack({
      sessionActive: session !== null,
      route: routeState,
    });
    if (action === "return_to_home") {
      router.replace("/home");
      return true;
    }
    if (action === "go_back") {
      router.back();
      return true;
    }
    return false;
  }, [routeState, router, session]);

  useEffect(() => {
    if (Platform.OS !== "android") return undefined;
    const subscription = BackHandler.addEventListener("hardwareBackPress", handleAndroidBack);
    return () => subscription.remove();
  }, [handleAndroidBack]);

  if (consentGate === "checking") {
    return (
      <GestureHandlerRootView style={styles.flex}>
        <SafeAreaView style={styles.safeArea} testID="app-consent-checking">
          <StatusBar barStyle="dark-content" backgroundColor={colors.canvas} />
          <View style={styles.centered}>
            <ActivityIndicator accessibilityLabel="正在检查使用条款状态" color={colors.accent} size="large" />
          </View>
        </SafeAreaView>
      </GestureHandlerRootView>
    );
  }

  if (consentGate === "needed") {
    return (
      <GestureHandlerRootView style={styles.flex}>
        <ConsentScreen onConsented={() => setConsentGate("done")} />
      </GestureHandlerRootView>
    );
  }

  if (sessionLoadState === "loading") {
    return (
      <GestureHandlerRootView style={styles.flex}>
        <SessionLoadScreen
          state="loading"
          onPairFresh={clearLocalSessionOrShowRecovery}
          onRetry={() => setSessionLoadAttempt((attempt) => attempt + 1)}
        />
      </GestureHandlerRootView>
    );
  }

  if (sessionLoadState === "failed") {
    return (
      <GestureHandlerRootView style={styles.flex}>
        <SessionLoadScreen
          state="failed"
          onPairFresh={clearLocalSessionOrShowRecovery}
          onRetry={() => setSessionLoadAttempt((attempt) => attempt + 1)}
        />
      </GestureHandlerRootView>
    );
  }

  if (!session) {
    return (
      <GestureHandlerRootView style={styles.flex}>
        <PairScreen onPaired={handlePaired} />
      </GestureHandlerRootView>
    );
  }

  return (
    <GestureHandlerRootView style={styles.flex}>
      <MobileCompanionProvider
        onRemoteInputGrant={handleRemoteInputGrant}
        onRemoteInputGrantRevoked={handleRemoteInputGrantRevoked}
        onSelectApproval={(approval) => router.push({ pathname: "/approval/[id]", params: { id: approval.id } })}
        onSessionExpired={clearLocalSessionOrShowRecovery}
        remoteInputGrant={remoteInputGrant}
        session={session}
      >
        <ApprovalNotificationRouter session={session} />
        <Slot />
      </MobileCompanionProvider>
    </GestureHandlerRootView>
  );
}

function ApprovalNotificationRouter({ session }: { session: PairingSession }) {
  const router = useRouter();
  const { updateApproval } = useMobileCompanion();

  useEffect(() => {
    let isActive = true;
    const openApprovalFromNotification = (approvalId: string) => {
      void getApprovalDetail(session, approvalId)
        .then((detail) => {
          if (!isActive) return;
          updateApproval(detail.approval);
          router.push({ pathname: "/approval/[id]", params: { id: detail.approval.id } });
        })
        .catch(() => undefined);
    };

    void getLastApprovalNotificationApprovalId()
      .then((lastApprovalId) => {
        if (lastApprovalId && isActive) openApprovalFromNotification(lastApprovalId);
      })
      .catch(() => undefined);

    const subscription = addApprovalNotificationResponseListener(openApprovalFromNotification);
    return () => {
      isActive = false;
      subscription.remove();
    };
  }, [router, session, updateApproval]);

  return null;
}

function routeStateFromPath(pathname: string) {
  if (pathname.startsWith("/approval/")) return { kind: "approvalDetail" as const };
  if (pathname.startsWith("/remote")) return { kind: "tab" as const, tab: "remote" as const };
  if (pathname.startsWith("/wakeups")) return { kind: "tab" as const, tab: "wakeups" as const };
  if (pathname.startsWith("/approvals")) return { kind: "tab" as const, tab: "approvals" as const };
  if (pathname.startsWith("/home")) return { kind: "tab" as const, tab: "home" as const };
  return { kind: "gate" as const };
}

function SessionLoadScreen({
  state,
  onPairFresh,
  onRetry,
}: {
  state: SessionLoadState;
  onPairFresh: () => void;
  onRetry: () => void;
}) {
  const isLoading = state === "loading";
  const title = isLoading ? "正在准备连接" : "无法恢复上次连接";
  const detail = isLoading ? "正在安全读取或清理这台手机保存的配对状态。" : "手机没有读到可用的本地会话。你可以重试，或重新和电脑配对。";
  return (
    <SafeAreaView style={styles.safeArea} testID="app-session-load-screen">
      <StatusBar barStyle="dark-content" backgroundColor={colors.canvas} />
      <View
        accessibilityLabel={`${title}。${detail}`}
        accessibilityLiveRegion="polite"
        accessibilityRole={isLoading ? "progressbar" : "alert"}
        style={styles.sessionLoadContent}
      >
        <View style={[styles.sessionLoadMark, !isLoading && styles.sessionLoadMarkFailed]}>
          {isLoading ? (
            <ActivityIndicator accessibilityLabel="正在准备连接" color={colors.accent} size="large" />
          ) : (
            <Text accessibilityElementsHidden importantForAccessibility="no" style={styles.sessionLoadMarkText}>
              !
            </Text>
          )}
        </View>
        <Text style={styles.sessionLoadTitle}>{title}</Text>
        <Text style={styles.sessionLoadText}>{detail}</Text>
        {!isLoading ? (
          <View style={styles.sessionLoadActions}>
            <Pressable
              accessibilityHint="再次读取手机本地保存的配对状态"
              accessibilityLabel="重试恢复连接"
              accessibilityRole="button"
              hitSlop={8}
              onPress={onRetry}
              style={({ pressed }) => [styles.secondaryButton, pressed && styles.pressed]}
              testID="app-session-retry-button"
            >
              <Text style={styles.secondaryButtonText}>重试</Text>
            </Pressable>
            <Pressable
              accessibilityHint="清理本地会话并回到配对页面"
              accessibilityLabel="重新配对"
              accessibilityRole="button"
              hitSlop={8}
              onPress={onPairFresh}
              style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed]}
              testID="app-session-pair-fresh-button"
            >
              <Text style={styles.primaryButtonText}>重新配对</Text>
            </Pressable>
          </View>
        ) : null}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: {
    flex: 1,
  },
  safeArea: {
    flex: 1,
    backgroundColor: colors.canvas,
  },
  centered: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  sessionLoadContent: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
  },
  sessionLoadMark: {
    width: 68,
    height: 68,
    borderRadius: 8,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 22,
  },
  sessionLoadMarkFailed: {
    backgroundColor: colors.dangerSoft,
  },
  sessionLoadMarkText: {
    color: colors.danger,
    fontSize: 32,
    fontWeight: "900",
  },
  sessionLoadTitle: {
    color: colors.ink,
    fontSize: 28,
    fontWeight: "900",
  },
  sessionLoadText: {
    color: colors.inkMuted,
    fontSize: 16,
    lineHeight: 23,
    marginTop: 8,
  },
  sessionLoadActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    marginTop: 24,
  },
  primaryButton: {
    minHeight: 48,
    borderRadius: 8,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 18,
  },
  primaryButtonText: {
    color: "#ffffff",
    fontSize: 15,
    fontWeight: "900",
  },
  secondaryButton: {
    minHeight: 48,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 18,
  },
  secondaryButtonText: {
    color: colors.accent,
    fontSize: 15,
    fontWeight: "900",
  },
  pressed: {
    opacity: 0.72,
  },
});
