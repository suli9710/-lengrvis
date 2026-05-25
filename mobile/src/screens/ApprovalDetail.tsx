import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { ArrowLeft, Check, X } from "lucide-react-native";

import {
  getApprovalDetail,
  submitApprovalDecision,
  type ApprovalDetail as ApprovalDetailData,
  type BackendApproval,
  type PairingSession,
} from "../api/client";
import { approvalStatusLabel, approvalTitle, formatPreview, shortDate } from "../format";

export function ApprovalDetail({
  session,
  approval,
  onBack,
  onUpdated,
}: {
  session: PairingSession;
  approval: BackendApproval;
  onBack: () => void;
  onUpdated: (approval: BackendApproval) => void;
}) {
  const [detail, setDetail] = useState<ApprovalDetailData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setIsLoading(true);
    getApprovalDetail(session, approval.id)
      .then((nextDetail) => {
        if (active) {
          setDetail(nextDetail);
          setError("");
        }
      })
      .catch((currentError: unknown) => {
        if (active) setError(errorMessage(currentError));
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [approval.id, session]);

  const currentApproval = detail?.approval ?? approval;
  const pending = currentApproval.status === "pending";
  const steps = useMemo(() => detail?.plan?.steps ?? [], [detail?.plan?.steps]);

  const handleDecision = async (decision: "approved" | "denied") => {
    setIsBusy(true);
    try {
      const updated = await submitApprovalDecision(session, currentApproval.id, decision);
      onUpdated(updated);
      onBack();
    } catch (currentError) {
      Alert.alert("Decision failed", errorMessage(currentError));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor="#f6f4ee" />
      <View style={styles.header}>
        <Pressable onPress={onBack} style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}>
          <ArrowLeft size={20} color="#23313d" />
        </Pressable>
        <View style={styles.headerText}>
          <Text style={styles.kicker}>{approvalStatusLabel(currentApproval.status)}</Text>
          <Text style={styles.headerTitle}>{approvalTitle(currentApproval)}</Text>
        </View>
      </View>

      {isLoading ? (
        <View style={styles.loading}>
          <ActivityIndicator color="#0e5f76" />
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content}>
          {error ? <Text style={styles.errorBanner}>{error}</Text> : null}

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Task</Text>
            <Text style={styles.body}>{detail?.task?.user_goal ?? currentApproval.message}</Text>
            <Text style={styles.meta}>Created {shortDate(currentApproval.created_at)}</Text>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Request</Text>
            <Text style={styles.body}>{currentApproval.message}</Text>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Plan Steps</Text>
            {steps.length ? (
              steps.map((step, index) => (
                <View key={step.id || index} style={styles.stepRow}>
                  <Text style={styles.stepIndex}>{index + 1}</Text>
                  <View style={styles.stepBody}>
                    <Text style={styles.stepTitle}>{step.tool_name || step.agent_name}</Text>
                    <Text style={styles.stepText}>{step.description}</Text>
                    <Text style={styles.meta}>{step.status}</Text>
                  </View>
                </View>
              ))
            ) : (
              <Text style={styles.muted}>No plan steps available yet.</Text>
            )}
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Dry-run Preview</Text>
            <Text style={styles.preview}>{formatPreview(detail?.preview ?? currentApproval.diff_preview)}</Text>
          </View>
        </ScrollView>
      )}

      {pending ? (
        <View style={styles.decisionRow}>
          <Pressable disabled={isBusy} onPress={() => void handleDecision("denied")} style={({ pressed }) => [styles.denyButton, pressed && styles.pressed]}>
            <X size={18} color="#8c2f39" />
            <Text style={styles.denyText}>Deny</Text>
          </Pressable>
          <Pressable disabled={isBusy} onPress={() => void handleDecision("approved")} style={({ pressed }) => [styles.approveButton, pressed && styles.pressed]}>
            {isBusy ? <ActivityIndicator color="#ffffff" /> : <Check size={18} color="#ffffff" />}
            <Text style={styles.approveText}>Approve</Text>
          </Pressable>
        </View>
      ) : null}
    </SafeAreaView>
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
    gap: 12,
    alignItems: "center",
  },
  backButton: {
    width: 42,
    height: 42,
    borderRadius: 8,
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "#d7dedf",
  },
  headerText: {
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
    fontSize: 25,
    fontWeight: "800",
    marginTop: 2,
  },
  loading: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  content: {
    padding: 20,
    paddingBottom: 120,
    gap: 14,
  },
  section: {
    borderRadius: 8,
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#d7dedf",
    padding: 16,
    gap: 10,
  },
  sectionTitle: {
    color: "#1f2933",
    fontSize: 13,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  body: {
    color: "#27343f",
    lineHeight: 22,
    fontSize: 15,
  },
  meta: {
    color: "#65717c",
    fontSize: 12,
  },
  muted: {
    color: "#65717c",
    lineHeight: 20,
  },
  stepRow: {
    flexDirection: "row",
    gap: 10,
  },
  stepIndex: {
    width: 26,
    height: 26,
    borderRadius: 8,
    overflow: "hidden",
    backgroundColor: "#e7ece8",
    color: "#1f2933",
    textAlign: "center",
    textAlignVertical: "center",
    fontWeight: "800",
  },
  stepBody: {
    flex: 1,
    minWidth: 0,
  },
  stepTitle: {
    color: "#1f2933",
    fontWeight: "800",
    marginBottom: 3,
  },
  stepText: {
    color: "#46535f",
    lineHeight: 20,
  },
  preview: {
    color: "#46535f",
    backgroundColor: "#f3f6f7",
    borderRadius: 8,
    padding: 12,
    lineHeight: 20,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: undefined }),
  },
  errorBanner: {
    color: "#8c2f39",
    lineHeight: 20,
  },
  decisionRow: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 20,
    backgroundColor: "#f6f4ee",
    borderTopWidth: 1,
    borderTopColor: "#d7dedf",
    flexDirection: "row",
    gap: 10,
  },
  denyButton: {
    flex: 1,
    minHeight: 48,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#e1b8be",
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
  },
  approveButton: {
    flex: 1,
    minHeight: 48,
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
