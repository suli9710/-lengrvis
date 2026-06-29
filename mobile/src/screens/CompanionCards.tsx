import type { ReactNode } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { ChevronRight, Pause, Play, Send, XCircle } from "lucide-react-native";

import type { BackendApproval, MobileTask, PairingSession, RemoteInputGrant } from "../api/client";
import { approvalListSafety } from "../approvalSafetyDisplay";
import { approvalStatusLabel, approvalTitle, formatPreview, shortDate } from "../format";
import { isRemoteInputGrantUsable } from "../remoteInputGrant";
import { safeDisplayText, safePreviewText } from "../safeDisplay";
import {
  taskActionAllowed,
  taskCredibilityText,
  taskDisplaySummary,
  taskDisplayTitle,
  taskNextStepText,
  taskStatusBadgeIsDone,
  taskStatusBadgeText,
} from "../taskCompanionDisplay";
import { ActionButton, Panel, StatusPill } from "../ui/Primitives";
import { colors, radii } from "../ui/theme";

export function ApprovalCard({
  approval,
  remoteInputGrant,
  session,
  onPress,
}: {
  approval: BackendApproval;
  remoteInputGrant: RemoteInputGrant | null;
  session: PairingSession;
  onPress: () => void;
}) {
  const pending = approval.status === "pending";
  const activeRemoteInputGrant = isRemoteInputGrantUsable(remoteInputGrant) ? remoteInputGrant : null;
  const safety = approvalListSafety(
    approval,
    activeRemoteInputGrant ? { deviceId: session.deviceId, grantId: activeRemoteInputGrant.id, bindingRef: activeRemoteInputGrant.binding_ref } : null,
  );
  const tone = safety.tone === "danger" ? "danger" : safety.tone === "warning" ? "warning" : "success";
  return (
    <Pressable
      accessibilityHint={safety.approveBlockedReason ? "打开详情后仍只能拒绝或回电脑端处理。" : "打开查看审批详情和安全核对。"}
      accessibilityLabel={`${approvalCardTitle(approval)}，${pending ? safety.label : approvalStatusText(approval)}`}
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [styles.cardPressable, pressed && styles.pressed]}
    >
      <Panel style={styles.cardPanel}>
        <View style={styles.cardHeader}>
          <View style={styles.cardTitleWrap}>
            <Text numberOfLines={2} style={styles.cardTitle}>{approvalCardTitle(approval)}</Text>
            <Text style={styles.cardMeta}>{shortDate(approval.created_at)}</Text>
          </View>
          <View style={styles.statusWrap}>
            <StatusPill label={pending ? safety.label : approvalStatusText(approval)} tone={pending ? tone : "neutral"} />
            <ChevronRight size={18} color={colors.inkSubtle} />
          </View>
        </View>
        <Text numberOfLines={3} style={styles.cardMessage}>{safeDisplayText(approval.message, "打开后查看这项审批。")}</Text>
        <View style={[styles.safetyStrip, tone === "danger" && styles.safetyDanger, tone === "warning" && styles.safetyWarning]}>
          <Text style={styles.safetyTitle}>{safety.label}</Text>
          <Text numberOfLines={2} style={styles.safetyText}>{safety.detail}</Text>
        </View>
        <Text numberOfLines={3} style={styles.preview}>{safePreviewText(formatPreview(approval.diff_preview))}</Text>
      </Panel>
    </Pressable>
  );
}

export function TaskCompanionCard({
  task,
  actionId,
  followUpBusy,
  followUpValue,
  onAction,
  onFollowUp,
  onFollowUpTextChange,
}: {
  task: MobileTask;
  actionId: string;
  followUpBusy: boolean;
  followUpValue: string;
  onAction: (task: MobileTask, action: "pause" | "resume" | "cancel") => Promise<void>;
  onFollowUp: (task: MobileTask, instruction: string) => Promise<void>;
  onFollowUpTextChange: (text: string) => void;
}) {
  const badgeDone = taskStatusBadgeIsDone(task);
  const actionBusy = actionId.startsWith(`${task.id}:`);
  const followUpAllowed = taskActionAllowed(task, "follow_up");
  const followUpDisabled = followUpBusy || !followUpValue.trim() || !followUpAllowed;
  const showResume = task.status === "paused" || taskActionAllowed(task, "resume");
  return (
    <Panel style={styles.taskPanel}>
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleWrap}>
          <Text numberOfLines={2} style={styles.cardTitle}>{taskDisplayTitle(task)}</Text>
          <Text style={styles.cardMeta}>{taskModeText(task.mode)} / {shortDate(task.updated_at)}</Text>
        </View>
        <StatusPill label={taskStatusBadgeText(task)} tone={badgeDone ? "success" : "warning"} />
      </View>
      <Text numberOfLines={3} style={styles.cardMessage}>{taskDisplaySummary(task)}</Text>
      <View style={styles.signalGrid}>
        <Signal label="可信度" text={taskCredibilityText(task)} />
        <Signal label="安全下一步" text={taskNextStepText(task)} />
      </View>
      <View style={styles.followUpRow}>
        <TextInput
          accessibilityLabel="补充任务指令"
          editable={followUpAllowed && !followUpBusy}
          multiline
          onChangeText={onFollowUpTextChange}
          placeholder={followUpAllowed ? "补充下一步，不要输入密码或 token。" : "此任务当前不能补充指令。"}
          placeholderTextColor={colors.inkSubtle}
          style={[styles.followUpInput, !followUpAllowed && styles.disabledInput]}
          value={followUpValue}
        />
        <Pressable
          accessibilityLabel="发送补充任务指令"
          accessibilityRole="button"
          accessibilityState={{ busy: followUpBusy, disabled: followUpDisabled }}
          disabled={followUpDisabled}
          hitSlop={4}
          onPress={() => void onFollowUp(task, followUpValue)}
          style={({ pressed }) => [styles.sendButton, followUpDisabled && styles.disabledAction, pressed && styles.pressed]}
        >
          <Send size={16} color="#ffffff" />
        </Pressable>
      </View>
      <View style={styles.taskActions}>
        {showResume ? (
          <TaskActionButton
            disabled={actionBusy || !taskActionAllowed(task, "resume")}
            icon={<Play size={15} color={colors.success} />}
            label="继续"
            onPress={() => void onAction(task, "resume")}
          />
        ) : (
          <TaskActionButton
            disabled={actionBusy || !taskActionAllowed(task, "pause")}
            icon={<Pause size={15} color={colors.warning} />}
            label="暂停"
            onPress={() => void onAction(task, "pause")}
          />
        )}
        <TaskActionButton
          disabled={actionBusy || !taskActionAllowed(task, "cancel")}
          icon={<XCircle size={15} color={colors.danger} />}
          label="取消"
          onPress={() => void onAction(task, "cancel")}
        />
      </View>
    </Panel>
  );
}

function Signal({ label, text }: { label: string; text: string }) {
  return (
    <View style={styles.signal}>
      <Text style={styles.signalLabel}>{label}</Text>
      <Text style={styles.signalText}>{text}</Text>
    </View>
  );
}

function TaskActionButton({
  disabled,
  icon,
  label,
  onPress,
}: {
  disabled: boolean;
  icon: ReactNode;
  label: string;
  onPress: () => void;
}) {
  return <ActionButton disabled={disabled} icon={icon} label={label} onPress={onPress} tone="neutral" />;
}

function approvalStatusText(approval: BackendApproval): string {
  if (approval.status === "pending") return "待审批";
  return approvalStatusLabel(approval.status);
}

function approvalCardTitle(approval: BackendApproval): string {
  if (approval.approval_type === "tool_call") return "审批请求";
  return approvalTitle(approval).replace("工具审批", "审批请求");
}

function taskModeText(mode: string): string {
  if (mode === "privacy") return "隐私";
  if (mode === "hybrid") return "混合";
  return "快速";
}

const styles = StyleSheet.create({
  cardPressable: {
    maxWidth: "100%",
  },
  cardPanel: {
    gap: 12,
  },
  taskPanel: {
    gap: 12,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 10,
  },
  cardTitleWrap: {
    flex: 1,
    minWidth: 0,
  },
  cardTitle: {
    color: colors.ink,
    fontSize: 17,
    fontWeight: "900",
    lineHeight: 21,
  },
  cardMeta: {
    color: colors.inkSubtle,
    marginTop: 3,
    fontSize: 12,
    fontWeight: "700",
  },
  statusWrap: {
    flexShrink: 1,
    maxWidth: "48%",
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  cardMessage: {
    color: colors.inkMuted,
    lineHeight: 21,
    fontSize: 14,
  },
  safetyStrip: {
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: "#a6ceb7",
    backgroundColor: colors.successSoft,
    paddingHorizontal: 10,
    paddingVertical: 8,
    gap: 3,
  },
  safetyWarning: {
    borderColor: "#e0c676",
    backgroundColor: colors.warningSoft,
  },
  safetyDanger: {
    borderColor: "#e4aaba",
    backgroundColor: colors.dangerSoft,
  },
  safetyTitle: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "900",
  },
  safetyText: {
    color: colors.inkMuted,
    fontSize: 12,
    lineHeight: 17,
  },
  preview: {
    color: colors.inkMuted,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.md,
    padding: 11,
    lineHeight: 19,
  },
  signalGrid: {
    gap: 8,
  },
  signal: {
    borderRadius: radii.md,
    backgroundColor: colors.surfaceMuted,
    padding: 10,
    gap: 3,
  },
  signalLabel: {
    color: colors.inkSubtle,
    fontSize: 11,
    fontWeight: "900",
  },
  signalText: {
    color: colors.ink,
    fontSize: 13,
    lineHeight: 19,
  },
  followUpRow: {
    flexDirection: "row",
    alignItems: "stretch",
    gap: 8,
  },
  followUpInput: {
    flex: 1,
    minHeight: 48,
    maxHeight: 84,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: "#ffffff",
    color: colors.ink,
    fontSize: 13,
    lineHeight: 19,
    paddingHorizontal: 10,
    paddingVertical: 8,
    textAlignVertical: "top",
  },
  disabledInput: {
    backgroundColor: colors.surfaceMuted,
    color: colors.inkSubtle,
  },
  sendButton: {
    width: 48,
    minHeight: 48,
    borderRadius: radii.md,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  taskActions: {
    flexDirection: "row",
    gap: 8,
  },
  disabledAction: {
    opacity: 0.44,
  },
  pressed: {
    opacity: 0.72,
  },
});
