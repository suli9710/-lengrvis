import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { ArrowLeft, Check, RefreshCcw, X } from "lucide-react-native";

import {
  AuthExpiredError,
  ForbiddenError,
  getApprovalDetail,
  submitApprovalDecision,
  type ApprovalDetail as ApprovalDetailData,
  type BackendApproval,
  type PairingSession,
  type RemoteInputGrant,
} from "../api/client";
import {
  approvalApproveBlockedReason,
  approvalDecisionGuard,
  remoteInputMobileDecisionBlockedReason,
  type ApprovalActiveGrantContext,
  type ApprovalDecisionGuardCopy,
} from "../approvalSafetyDisplay";
import { approvalStatusLabel, approvalTitle, formatPreview, shortDate } from "../format";
import { isRemoteInputGrantUsable } from "../remoteInputGrant";
import { safeCompactText, safeDisplayText, safePreviewText } from "../safeDisplay";
import { colors, radii } from "../ui/theme";

export function ApprovalDetail({
  session,
  approval,
  onBack,
  onUpdated,
  onSessionExpired,
  remoteInputGrant,
}: {
  session: PairingSession;
  approval: BackendApproval;
  onBack: () => void;
  onUpdated: (approval: BackendApproval) => void;
  onSessionExpired: () => void;
  remoteInputGrant?: RemoteInputGrant | null;
}) {
  const [detail, setDetail] = useState<ApprovalDetailData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const submitLockRef = useRef(false);

  useEffect(() => {
    let active = true;
    setIsLoading(true);
    setDetail((current) => (current?.approval.id === approval.id ? current : null));
    getApprovalDetail(session, approval.id)
      .then((nextDetail) => {
        if (active) {
          setDetail(nextDetail);
          setError("");
        }
      })
      .catch((currentError: unknown) => {
        if (!active) return;
        if (currentError instanceof AuthExpiredError) {
          onSessionExpired();
          return;
        }
        setError(errorMessage(currentError));
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [approval.id, onSessionExpired, reloadKey, session]);

  const retryLoadDetail = useCallback(() => {
    setError("");
    setReloadKey((current) => current + 1);
  }, []);

  const currentApproval = detail?.approval ?? approval;
  const pending = currentApproval.status === "pending";
  const steps = useMemo(() => detail?.plan?.steps ?? [], [detail?.plan?.steps]);
  const usableRemoteInputGrant = useMemo(() => isRemoteInputGrantUsable(remoteInputGrant) ? remoteInputGrant : null, [remoteInputGrant]);
  const activeGrantContext = useMemo<ApprovalActiveGrantContext | null>(
    () => usableRemoteInputGrant ? { deviceId: session.deviceId, grantId: usableRemoteInputGrant.id, bindingRef: usableRemoteInputGrant.binding_ref } : null,
    [usableRemoteInputGrant, session.deviceId],
  );
  const decisionGuard = useMemo(() => approvalDecisionGuard(currentApproval, activeGrantContext), [activeGrantContext, currentApproval]);
  const approveBlockedReason = useMemo(() => approvalApproveBlockedReason(currentApproval, activeGrantContext), [activeGrantContext, currentApproval]);
  const mobileDecisionBlockedReason = useMemo(
    () => remoteInputMobileDecisionBlockedReason(currentApproval, activeGrantContext),
    [activeGrantContext, currentApproval],
  );
  const canShowDecisionRow = pending && !isLoading && Boolean(detail) && !error;

  const handleDecision = async (decision: "approved" | "denied") => {
    if (submitLockRef.current) return;
    submitLockRef.current = true;
    setIsBusy(true);
    try {
      const latest = await getApprovalDetail(session, currentApproval.id);
      setDetail(latest);
      if (latest.approval.status !== "pending") {
        onUpdated(latest.approval);
        Alert.alert("审批已处理", `此审批当前状态为：${approvalStatusLabel(latest.approval.status)}。`);
        return;
      }
      const latestBlockedReason = approvalApproveBlockedReason(latest.approval, activeGrantContext);
      const latestMobileDecisionBlockedReason = remoteInputMobileDecisionBlockedReason(latest.approval, activeGrantContext);
      if (decision === "approved" && latestMobileDecisionBlockedReason) {
        setDetail(latest);
        Alert.alert("请回电脑端处理", latestMobileDecisionBlockedReason);
        return;
      }
      const latestApproveBlockedReason = decision === "approved" ? latestBlockedReason : null;
      if (latestApproveBlockedReason) {
        setDetail(latest);
        Alert.alert("手机端不可批准", latestApproveBlockedReason);
        return;
      }
      const updated = await submitApprovalDecision(session, latest.approval.id, decision, {
        approval: latest.approval,
        approvalType: latest.approval.approval_type,
        remoteInputGrant: usableRemoteInputGrant,
      });
      onUpdated(updated);
      onBack();
    } catch (currentError) {
      if (currentError instanceof AuthExpiredError) {
        onSessionExpired();
        return;
      }
      Alert.alert("提交失败", errorMessage(currentError));
    } finally {
      setIsBusy(false);
      submitLockRef.current = false;
    }
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={styles.keyboardAvoiding}
    >
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor={colors.canvas} />
      <View style={styles.header}>
        <Pressable
          accessibilityLabel="返回审批列表"
          accessibilityRole="button"
          onPress={onBack}
          style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}
        >
          <ArrowLeft size={20} color="#23313d" />
        </Pressable>
        <View style={styles.headerText}>
          <Text style={styles.kicker}>{approvalStatusLabel(currentApproval.status)}</Text>
          <Text style={styles.headerTitle}>{approvalTitle(currentApproval)}</Text>
        </View>
      </View>

      {isLoading ? (
        <View accessible accessibilityLabel="正在加载审批详情" style={styles.loading}>
          <ActivityIndicator color="#0e5f76" />
          <Text style={styles.loadingText}>正在加载审批详情…</Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content}>
          {error ? <ApprovalDetailError error={error} hasCachedDetail={Boolean(detail)} onRetry={retryLoadDetail} /> : null}

          <ApprovalDecisionGuard guard={decisionGuard} />

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>请求</Text>
            <Text style={styles.body}>{safeDisplayText(currentApproval.message, "请求内容已隐藏，请在电脑端查看。")}</Text>
            <Text style={styles.meta}>创建于 {shortDate(currentApproval.created_at)}</Text>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>安全预览</Text>
            <Text style={styles.preview}>{safePreviewText(formatPreview(detail?.preview ?? currentApproval.diff_preview))}</Text>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>计划步骤</Text>
            {steps.length ? (
              steps.map((step, index) => (
                <View key={step.id || index} style={styles.stepRow}>
                  <Text style={styles.stepIndex}>{index + 1}</Text>
                  <View style={styles.stepBody}>
                    <Text style={styles.stepTitle}>{safeCompactText(step.tool_name || step.agent_name, "计划步骤")}</Text>
                    <Text style={styles.stepText}>{safeDisplayText(step.description, "步骤细节已隐藏，请在电脑端查看。")}</Text>
                    <Text style={styles.meta}>
                      {[
                        safeCompactText(step.status, "状态未知"),
                        step.risk_level ? `风险 ${safeCompactText(step.risk_level, "已隐藏")}` : "",
                        step.trust_tier ? `可信级别 ${safeCompactText(step.trust_tier, "已隐藏")}` : "",
                        step.deferred_tool ? "需要电脑端继续确认" : "",
                      ].filter(Boolean).join(" · ")}
                    </Text>
                  </View>
                </View>
              ))
            ) : (
              <Text style={styles.muted}>暂无计划步骤。</Text>
            )}
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>任务目标</Text>
            <Text style={styles.body}>{safeDisplayText(detail?.task?.user_goal ?? currentApproval.message, "任务内容已隐藏，请在电脑端查看。")}</Text>
          </View>

          <ApprovalBoundarySection approval={currentApproval} />
        </ScrollView>
      )}

      {canShowDecisionRow ? (
        <View style={styles.decisionRow}>
          <Pressable
            accessibilityHint="拒绝后电脑端不会继续执行此审批。"
            accessibilityLabel="拒绝审批"
            accessibilityRole="button"
            accessibilityState={{ disabled: isBusy, busy: isBusy }}
            disabled={isBusy}
            onPress={() => void handleDecision("denied")}
            style={({ pressed }) => [
              styles.denyButton,
              pressed && styles.pressed,
            ]}
          >
            <X size={18} color="#8c2f39" />
            <Text style={styles.denyText}>拒绝</Text>
          </Pressable>
          <Pressable
            accessibilityHint={mobileDecisionBlockedReason || approveBlockedReason || decisionGuard.nextStep}
            accessibilityLabel={mobileDecisionBlockedReason || approveBlockedReason ? "手机端不可批准此审批" : "批准审批"}
            accessibilityRole="button"
            accessibilityState={{ disabled: isBusy || Boolean(mobileDecisionBlockedReason || approveBlockedReason), busy: isBusy }}
            disabled={isBusy || Boolean(mobileDecisionBlockedReason || approveBlockedReason)}
            onPress={() => void handleDecision("approved")}
            style={({ pressed }) => [
              styles.approveButton,
              (mobileDecisionBlockedReason || approveBlockedReason) && styles.disabledApproveButton,
              pressed && !mobileDecisionBlockedReason && !approveBlockedReason && styles.pressed,
            ]}
          >
            {isBusy ? <ActivityIndicator color="#ffffff" /> : <Check size={18} color="#ffffff" />}
            <Text style={styles.approveText}>{mobileDecisionBlockedReason || approveBlockedReason ? "不可批准" : "批准"}</Text>
          </Pressable>
        </View>
      ) : null}
    </SafeAreaView>
    </KeyboardAvoidingView>
  );
}

function ApprovalDecisionGuard({ guard }: { guard: ApprovalDecisionGuardCopy }) {
  return (
    <View
      accessible
      accessibilityLabel={`批准前核对：${guard.title}。${guard.detail}。${guard.approveBlockedReason || guard.nextStep}`}
      style={[
        styles.decisionGuard,
        guard.tone === "danger" && styles.decisionGuardDanger,
        guard.tone === "warning" && styles.decisionGuardWarning,
      ]}
    >
      <Text style={styles.decisionGuardKicker}>批准前核对</Text>
      <Text style={styles.decisionGuardTitle}>{guard.title}</Text>
      <Text style={styles.decisionGuardText}>{guard.detail}</Text>
      <Text style={styles.decisionGuardNext}>{guard.approveBlockedReason || guard.nextStep}</Text>
    </View>
  );
}

function ApprovalDetailError({
  error,
  hasCachedDetail,
  onRetry,
}: {
  error: string;
  hasCachedDetail: boolean;
  onRetry: () => void;
}) {
  return (
    <View accessibilityRole="alert" style={styles.errorPanel}>
      <Text style={styles.errorBanner}>{error}</Text>
      {hasCachedDetail ? null : (
        <Text style={styles.errorHint}>已保留列表里的审批摘要。完整计划和试运行预览需要重新加载。</Text>
      )}
      <Pressable
        accessibilityLabel="重新加载审批详情"
        accessibilityRole="button"
        onPress={onRetry}
        style={({ pressed }) => [styles.retryButton, pressed && styles.pressed]}
      >
        <RefreshCcw size={15} color="#23313d" />
        <Text style={styles.retryText}>重新加载详情</Text>
      </Pressable>
    </View>
  );
}

function ApprovalBoundarySection({ approval }: { approval: BackendApproval }) {
  const boundary = objectValue(approval.engineering_boundary);
  const tool = objectValue(boundary.tool);
  const dryRun = objectValue(boundary.dry_run);
  const policy = objectValue(boundary.policy);
  const action = safeCompactText(approval.tool_name || textValue(tool.name) || approval.approval_type, "审批动作");
  const risk = safeCompactText(approval.risk_level || textValue(tool.risk_level), "未提供");
  const trustTier = safeCompactText(approval.tool_trust_tier || textValue(tool.trust_tier), "未提供");
  const policyMode = safeCompactText(approval.policy_mode || approval.permission_mode || textValue(boundary.policy_mode), "默认");
  const effects = approval.tool_effects?.length ? approval.tool_effects : stringList(tool.effects);
  const resources = approval.resource_kinds?.length ? approval.resource_kinds : stringList(tool.resource_kinds);
  const dryRunSummary = safeDisplayText(approval.dry_run_summary || textValue(dryRun.summary), "暂无安全试运行摘要。");
  const policyReason = safeDisplayText(textValue(boundary.policy_reason) || textValue(policy.reason) || approval.message, "策略原因已隐藏，请在电脑端查看。");

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>工程边界</Text>
      <View style={styles.boundaryGrid}>
        <BoundaryFact label="动作" value={action} />
        <BoundaryFact label="风险" value={risk} />
        <BoundaryFact label="可信级别" value={trustTier} />
        <BoundaryFact label="权限模式" value={policyMode} />
      </View>
      <ChipRow label="影响类型" values={effects} emptyText="未声明影响类型" />
      <ChipRow label="资源范围" values={resources} emptyText="未声明资源范围" />
      <View style={styles.boundaryBlock}>
        <Text style={styles.boundaryLabel}>安全预览</Text>
        <Text style={styles.boundaryText}>{dryRunSummary}</Text>
      </View>
      <View style={styles.boundaryBlock}>
        <Text style={styles.boundaryLabel}>策略原因</Text>
        <Text style={styles.boundaryText}>{policyReason}</Text>
      </View>
    </View>
  );
}

function BoundaryFact({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.boundaryFact}>
      <Text style={styles.boundaryLabel}>{label}</Text>
      <Text style={styles.boundaryValue}>{value}</Text>
    </View>
  );
}

function ChipRow({ label, values, emptyText }: { label: string; values: string[]; emptyText: string }) {
  const visible = values.length ? values.map((value) => safeCompactText(value, "已隐藏")) : [emptyText];
  return (
    <View style={styles.chipRowBlock}>
      <Text style={styles.boundaryLabel}>{label}</Text>
      <View style={styles.chipRow}>
        {visible.map((value) => (
          <Text key={`${label}-${value}`} style={styles.chip}>{value}</Text>
        ))}
      </View>
    </View>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ForbiddenError) {
    return "这台手机没有权限查看或处理此审批。请在电脑端重新配对后再试。";
  }
  if (error instanceof Error && error.message.includes("Failed to fetch")) {
    return "无法连接到电脑。请确认 Lengrvis 已打开，然后重试。";
  }
  if (error instanceof Error && error.message.toLowerCase().includes("network")) {
    return "网络连接异常。请确认手机和电脑在同一网络后重试。";
  }
  return "请求失败。请返回列表刷新后重试。";
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function textValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(textValue).filter(Boolean);
}

const styles = StyleSheet.create({
  keyboardAvoiding: {
    flex: 1,
    backgroundColor: colors.canvas,
  },
  safeArea: {
    flex: 1,
    backgroundColor: colors.canvas,
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
    borderRadius: radii.md,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  headerText: {
    flex: 1,
    minWidth: 0,
  },
  kicker: {
    color: colors.inkSubtle,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  headerTitle: {
    color: colors.ink,
    fontSize: 25,
    fontWeight: "900",
    marginTop: 2,
  },
  loading: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
    paddingHorizontal: 24,
  },
  loadingText: {
    color: colors.inkMuted,
    fontWeight: "900",
    textAlign: "center",
  },
  content: {
    padding: 20,
    paddingBottom: Platform.select({ android: 152, default: 128 }),
    gap: 14,
  },
  section: {
    borderRadius: radii.md,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 16,
    gap: 10,
  },
  sectionTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  body: {
    color: colors.inkMuted,
    lineHeight: 22,
    fontSize: 15,
  },
  meta: {
    color: colors.inkSubtle,
    fontSize: 12,
  },
  muted: {
    color: colors.inkSubtle,
    lineHeight: 20,
  },
  stepRow: {
    flexDirection: "row",
    gap: 10,
  },
  stepIndex: {
    width: 26,
    height: 26,
    borderRadius: radii.md,
    overflow: "hidden",
    backgroundColor: colors.surfaceMuted,
    color: colors.ink,
    textAlign: "center",
    textAlignVertical: "center",
    fontWeight: "800",
  },
  stepBody: {
    flex: 1,
    minWidth: 0,
  },
  stepTitle: {
    color: colors.ink,
    fontWeight: "900",
    marginBottom: 3,
  },
  stepText: {
    color: colors.inkMuted,
    lineHeight: 20,
  },
  preview: {
    color: colors.inkMuted,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.md,
    padding: 12,
    lineHeight: 20,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: undefined }),
  },
  decisionGuard: {
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    padding: 16,
    gap: 8,
  },
  decisionGuardWarning: {
    borderColor: "#e0c676",
    backgroundColor: colors.warningSoft,
  },
  decisionGuardDanger: {
    borderColor: "#e4aaba",
    backgroundColor: colors.dangerSoft,
  },
  decisionGuardKicker: {
    color: colors.inkSubtle,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  decisionGuardTitle: {
    color: colors.ink,
    fontSize: 16,
    fontWeight: "900",
  },
  decisionGuardText: {
    color: colors.inkMuted,
    lineHeight: 21,
  },
  decisionGuardNext: {
    color: colors.danger,
    lineHeight: 21,
    fontWeight: "900",
  },
  boundaryGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  boundaryFact: {
    flexGrow: 1,
    flexBasis: "46%",
    minWidth: 130,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceMuted,
    padding: 10,
    gap: 4,
  },
  boundaryLabel: {
    color: colors.inkSubtle,
    fontSize: 11,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  boundaryValue: {
    color: colors.ink,
    fontSize: 14,
    fontWeight: "900",
  },
  boundaryBlock: {
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceMuted,
    padding: 10,
    gap: 6,
  },
  boundaryText: {
    color: colors.inkMuted,
    lineHeight: 20,
  },
  chipRowBlock: {
    gap: 8,
  },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  chip: {
    maxWidth: "100%",
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceMuted,
    color: colors.inkMuted,
    fontSize: 12,
    fontWeight: "800",
    paddingHorizontal: 8,
    paddingVertical: 5,
  },
  errorBanner: {
    color: colors.danger,
    lineHeight: 20,
  },
  errorPanel: {
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: "#e4aaba",
    backgroundColor: colors.dangerSoft,
    padding: 12,
    gap: 9,
  },
  errorHint: {
    color: colors.inkMuted,
    lineHeight: 20,
  },
  retryButton: {
    alignSelf: "flex-start",
    minHeight: 36,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
    paddingHorizontal: 12,
  },
  retryText: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "900",
  },
  decisionRow: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: Platform.select({ android: 32, default: 20 }),
    backgroundColor: colors.canvas,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    flexDirection: "row",
    gap: 10,
  },
  denyButton: {
    flex: 1,
    minWidth: 0,
    minHeight: 48,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: "#e4aaba",
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
  },
  approveButton: {
    flex: 1,
    minWidth: 0,
    minHeight: 48,
    borderRadius: radii.md,
    backgroundColor: colors.success,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
  },
  disabledApproveButton: {
    backgroundColor: colors.inkSubtle,
    opacity: 0.72,
  },
  disabledDenyButton: {
    borderColor: colors.border,
    backgroundColor: colors.surfaceMuted,
    opacity: 0.72,
  },
  denyText: {
    flexShrink: 1,
    color: colors.danger,
    fontWeight: "900",
    textAlign: "center",
  },
  approveText: {
    flexShrink: 1,
    color: "#ffffff",
    fontWeight: "900",
    textAlign: "center",
  },
  pressed: {
    opacity: 0.72,
  },
});
