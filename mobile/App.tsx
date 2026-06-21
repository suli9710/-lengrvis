import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, BackHandler, Platform, Pressable, SafeAreaView, StatusBar, StyleSheet, Text, View } from "react-native";

import { clearRemoteInputGrantTokens, getApprovalDetail, type BackendApproval, type PairingSession, type RemoteInputGrant } from "./src/api/client";
import { resolveAndroidBack } from "./src/androidBackNavigation";
import { addApprovalNotificationResponseListener, getLastApprovalNotificationApprovalId } from "./src/notifications";
import { ApprovalDetail } from "./src/screens/ApprovalDetail";
import { ApprovalsScreen } from "./src/screens/ApprovalsScreen";
import { ConsentScreen } from "./src/screens/ConsentScreen";
import { PairScreen } from "./src/screens/PairScreen";
import { RemoteScreen } from "./src/screens/RemoteScreen";
import { WakeupsScreen } from "./src/screens/WakeupsScreen";
import { isRemoteInputGrantUsable, reduceRemoteInputGrant, remoteInputGrantExpiryDelayMs } from "./src/remoteInputGrant";
import { clearSession, loadSession } from "./src/store/auth";
import { loadConsentState } from "./src/store/consent";

type ActiveScreen = "approvals" | "remote" | "wakeups";
type SessionLoadState = "loading" | "ready" | "failed";
type ConsentGateState = "checking" | "needed" | "done";

export default function App() {
  const [session, setSession] = useState<PairingSession | null>(null);
  const [sessionLoadState, setSessionLoadState] = useState<SessionLoadState>("loading");
  const [sessionLoadAttempt, setSessionLoadAttempt] = useState(0);
  const [selectedApproval, setSelectedApproval] = useState<BackendApproval | null>(null);
  const [activeScreen, setActiveScreen] = useState<ActiveScreen>("approvals");
  const [remoteInputGrant, setRemoteInputGrant] = useState<RemoteInputGrant | null>(null);
  const [consentGate, setConsentGate] = useState<ConsentGateState>("checking");

  // --- Consent gate: check before anything else ---
  useEffect(() => {
    let isActive = true;
    void loadConsentState()
      .then((state) => {
        if (!isActive) return;
        setConsentGate(state.needsConsent ? "needed" : "done");
      })
      .catch(() => {
        if (!isActive) return;
        // If consent check fails, err on the side of showing the consent screen.
        setConsentGate("needed");
      });
    return () => { isActive = false; };
  }, []);

  const handleConsented = useCallback(() => {
    setConsentGate("done");
  }, []);

  useEffect(() => {
    if (consentGate !== "done") return undefined;
    let isActive = true;
    setSessionLoadState("loading");
    void loadSession()
      .then((stored) => {
        if (!isActive) return;
        if (!stored) {
          setSelectedApproval(null);
          setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "cleared" }));
          setActiveScreen("approvals");
        }
        setSession(stored);
        setSessionLoadState("ready");
      })
      .catch(() => {
        if (!isActive) return;
        setSelectedApproval(null);
        setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "cleared" }));
        setActiveScreen("approvals");
        setSession(null);
        setSessionLoadState("failed");
      });
    return () => {
      isActive = false;
    };
  }, [sessionLoadAttempt, consentGate]);

  useEffect(() => {
    if (!session) return undefined;
    let isActive = true;
    const openApprovalFromNotification = (approvalId: string) => {
      void getApprovalDetail(session, approvalId)
        .then((detail) => {
          if (!isActive) return;
          setSelectedApproval(detail.approval);
          setActiveScreen("approvals");
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
  }, [session]);

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

  const handleAndroidBack = useCallback((): boolean => {
    const action = resolveAndroidBack({
      sessionActive: session !== null,
      activeScreen,
      hasSelectedApproval: selectedApproval !== null,
    });
    if (action === "return_to_approvals") {
      setActiveScreen("approvals");
      return true;
    }
    if (action === "close_approval_detail") {
      setSelectedApproval(null);
      return true;
    }
    return false;
  }, [session, activeScreen, selectedApproval]);

  useEffect(() => {
    if (Platform.OS !== "android") return undefined;
    const subscription = BackHandler.addEventListener("hardwareBackPress", handleAndroidBack);
    return () => subscription.remove();
  }, [handleAndroidBack]);

  const handleRemoteInputGrant = (grant: RemoteInputGrant) => {
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "received", grant }));
  };

  const handleRemoteInputGrantRevoked = (grant: RemoteInputGrant) => {
    clearRemoteInputGrantTokens();
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "revoked", grantId: grant.id }));
  };

  const resetShellState = () => {
    clearRemoteInputGrantTokens();
    setSelectedApproval(null);
    setRemoteInputGrant((current) => reduceRemoteInputGrant(current, { type: "cleared" }));
    setActiveScreen("approvals");
  };

  const clearLocalSessionOrShowRecovery = () => {
    resetShellState();
    setSession(null);
    setSessionLoadState("loading");
    void clearSession()
      .then(() => {
        setSessionLoadState("ready");
      })
      .catch(() => {
        setSessionLoadState("failed");
      });
  };

  const handleSessionExpired = () => {
    clearLocalSessionOrShowRecovery();
  };

  const handlePaired = (nextSession: PairingSession) => {
    resetShellState();
    setSessionLoadState("ready");
    setSession(nextSession);
  };

  const handleStartFreshPairing = () => {
    clearLocalSessionOrShowRecovery();
  };

  // --- Consent gate takes priority over everything else ---
  if (consentGate === "checking") {
    return (
      <SafeAreaView style={styles.safeArea} testID="app-consent-checking">
        <StatusBar barStyle="dark-content" backgroundColor="#f7f9fb" />
        <View style={styles.consentCheckingContent}>
          <ActivityIndicator accessibilityLabel="正在检查使用条款状态" color="#0e5f76" size="large" />
        </View>
      </SafeAreaView>
    );
  }

  if (consentGate === "needed") {
    return <ConsentScreen onConsented={handleConsented} />;
  }

  if (sessionLoadState === "loading") {
    return <SessionLoadScreen state="loading" onPairFresh={handleStartFreshPairing} onRetry={() => setSessionLoadAttempt((attempt) => attempt + 1)} />;
  }

  if (sessionLoadState === "failed") {
    return <SessionLoadScreen state="failed" onPairFresh={handleStartFreshPairing} onRetry={() => setSessionLoadAttempt((attempt) => attempt + 1)} />;
  }

  if (!session) {
    return <PairScreen onPaired={handlePaired} />;
  }

  if (activeScreen === "wakeups") {
    return (
      <WakeupsScreen
        onBack={() => setActiveScreen("approvals")}
        onSessionExpired={handleSessionExpired}
        session={session}
      />
    );
  }

  if (activeScreen === "remote") {
    return (
      <RemoteScreen
        grant={remoteInputGrant}
        onBack={() => setActiveScreen("approvals")}
        onRemoteInputGrantRevoked={handleRemoteInputGrantRevoked}
        onSessionExpired={handleSessionExpired}
        session={session}
      />
    );
  }

  if (selectedApproval) {
    return (
      <ApprovalDetail
        approval={selectedApproval}
        onBack={() => setSelectedApproval(null)}
        onSessionExpired={handleSessionExpired}
        onUpdated={setSelectedApproval}
        remoteInputGrant={remoteInputGrant}
        session={session}
      />
    );
  }

  return (
    <ApprovalsScreen
      onOpenRemote={() => setActiveScreen("remote")}
      onOpenWakeups={() => setActiveScreen("wakeups")}
      onRemoteInputGrant={handleRemoteInputGrant}
      onRemoteInputGrantRevoked={handleRemoteInputGrantRevoked}
      onSelectApproval={setSelectedApproval}
      onUnpair={clearLocalSessionOrShowRecovery}
      remoteInputGrant={remoteInputGrant}
      session={session}
    />
  );
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
      <StatusBar barStyle="dark-content" backgroundColor="#f7f9fb" />
      <View
        accessibilityLabel={`${title}。${detail}`}
        accessibilityLiveRegion="polite"
        accessibilityRole={isLoading ? "progressbar" : "alert"}
        style={styles.sessionLoadContent}
      >
        <View style={[styles.sessionLoadMark, !isLoading && styles.sessionLoadMarkFailed]}>
          {isLoading ? (
            <ActivityIndicator accessibilityLabel="正在准备连接" color="#0e5f76" size="large" />
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
  safeArea: {
    flex: 1,
    backgroundColor: "#f7f9fb",
  },
  consentCheckingContent: {
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
    borderRadius: 18,
    backgroundColor: "#e6f0ef",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 22,
  },
  sessionLoadMarkFailed: {
    backgroundColor: "#fff0f2",
  },
  sessionLoadMarkText: {
    color: "#8c2f39",
    fontSize: 32,
    fontWeight: "900",
  },
  sessionLoadTitle: {
    color: "#17323a",
    fontSize: 28,
    fontWeight: "800",
  },
  sessionLoadText: {
    color: "#52616d",
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
    backgroundColor: "#0e5f76",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 18,
  },
  primaryButtonText: {
    color: "#ffffff",
    fontSize: 15,
    fontWeight: "800",
  },
  secondaryButton: {
    minHeight: 48,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#9ec6cf",
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 18,
  },
  secondaryButtonText: {
    color: "#0e5f76",
    fontSize: 15,
    fontWeight: "800",
  },
  pressed: {
    opacity: 0.72,
  },
});
