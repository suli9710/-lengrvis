import type { ReactNode } from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
  type ViewStyle,
} from "react-native";
import type { LucideIcon } from "lucide-react-native";

import type { UiTone } from "../navigation/types";
import { colors, radii, shadows, spacing, text } from "./theme";

export function ScreenShell({
  children,
  scroll = true,
  testID,
  tone = "light",
  contentStyle,
}: {
  children: ReactNode;
  scroll?: boolean;
  testID?: string;
  tone?: "light" | "remote";
  contentStyle?: ViewStyle;
}) {
  const dark = tone === "remote";
  const body = <View style={[styles.content, !scroll && styles.flex, contentStyle]}>{children}</View>;
  return (
    <SafeAreaView style={[styles.safeArea, dark && styles.remoteSafeArea]} testID={testID}>
      <StatusBar barStyle={dark ? "light-content" : "dark-content"} backgroundColor={dark ? colors.remoteBg : colors.canvas} />
      {scroll ? (
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
          keyboardShouldPersistTaps="handled"
        >
          {body}
        </ScrollView>
      ) : body}
    </SafeAreaView>
  );
}

export function TopBar({
  kicker,
  title,
  detail,
  action,
  tone = "light",
}: {
  kicker: string;
  title: string;
  detail?: string;
  action?: ReactNode;
  tone?: "light" | "remote";
}) {
  const dark = tone === "remote";
  return (
    <View style={styles.topBar}>
      <View style={styles.topBarCopy}>
        <Text style={[text.kicker, dark && styles.remoteKicker]}>{kicker}</Text>
        <Text style={[text.title, dark && styles.remoteTitle]}>{title}</Text>
        {detail ? <Text style={[styles.topBarDetail, dark && styles.remoteDetail]}>{detail}</Text> : null}
      </View>
      {action}
    </View>
  );
}

export function IconButton({
  accessibilityLabel,
  icon,
  onPress,
  tone = "light",
  testID,
}: {
  accessibilityLabel: string;
  icon: ReactNode;
  onPress: () => void;
  tone?: "light" | "danger" | "remote";
  testID?: string;
}) {
  return (
    <Pressable
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="button"
      hitSlop={8}
      onPress={onPress}
      style={({ pressed }) => [
        styles.iconButton,
        tone === "danger" && styles.iconDanger,
        tone === "remote" && styles.iconRemote,
        pressed && styles.pressed,
      ]}
      testID={testID}
    >
      {icon}
    </Pressable>
  );
}

export function ActionButton({
  label,
  icon,
  onPress,
  disabled,
  busy,
  tone = "accent",
  testID,
  accessibilityLabel,
}: {
  label: string;
  icon?: ReactNode;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
  tone?: UiTone;
  testID?: string;
  accessibilityLabel?: string;
}) {
  const bgStyle =
    tone === "danger"
      ? styles.actionDanger
      : tone === "success"
        ? styles.actionSuccess
        : tone === "warning"
          ? styles.actionWarning
          : tone === "neutral"
            ? styles.actionNeutral
            : tone === "remote"
              ? styles.actionRemote
              : styles.actionAccent;
  const textStyle = tone === "neutral" ? styles.actionTextNeutral : styles.actionText;
  return (
    <Pressable
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityRole="button"
      accessibilityState={{ disabled: Boolean(disabled), busy: Boolean(busy) }}
      disabled={disabled || busy}
      hitSlop={8}
      onPress={onPress}
      style={({ pressed }) => [styles.actionButton, bgStyle, (disabled || busy) && styles.disabled, pressed && styles.pressed]}
      testID={testID}
    >
      {busy ? <ActivityIndicator color={tone === "neutral" ? colors.accent : "#ffffff"} /> : icon}
      <Text style={textStyle}>{label}</Text>
    </Pressable>
  );
}

export function StatusPill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: UiTone;
}) {
  const toneStyle =
    tone === "success"
      ? styles.pillSuccess
      : tone === "warning"
        ? styles.pillWarning
        : tone === "danger"
          ? styles.pillDanger
          : tone === "accent"
            ? styles.pillAccent
            : styles.pillNeutral;
  return <Text style={[styles.pill, toneStyle]}>{label}</Text>;
}

export function NoticeBanner({
  title,
  detail,
  icon: Icon,
  tone = "neutral",
  testID,
}: {
  title?: string;
  detail: string;
  icon?: LucideIcon;
  tone?: UiTone;
  testID?: string;
}) {
  const toneStyle =
    tone === "success"
      ? styles.noticeSuccess
      : tone === "warning"
        ? styles.noticeWarning
        : tone === "danger"
          ? styles.noticeDanger
          : styles.noticeNeutral;
  const iconColor = tone === "danger" ? colors.danger : tone === "warning" ? colors.warning : tone === "success" ? colors.success : colors.accent;
  return (
    <View accessibilityRole={tone === "danger" ? "alert" : undefined} style={[styles.notice, toneStyle]} testID={testID}>
      {Icon ? <Icon size={18} color={iconColor} /> : null}
      <View style={styles.noticeCopy}>
        {title ? <Text style={styles.noticeTitle}>{title}</Text> : null}
        <Text style={styles.noticeDetail}>{detail}</Text>
      </View>
    </View>
  );
}

export function MetricCard({
  label,
  value,
  detail,
  icon,
  tone = "neutral",
  onPress,
}: {
  label: string;
  value: string;
  detail?: string;
  icon?: ReactNode;
  tone?: UiTone;
  onPress?: () => void;
}) {
  const content = (
    <>
      <View style={styles.metricTop}>
        <Text style={styles.metricLabel}>{label}</Text>
        {icon}
      </View>
      <Text style={styles.metricValue}>{value}</Text>
      {detail ? <Text style={styles.metricDetail}>{detail}</Text> : null}
    </>
  );
  const toneStyle =
    tone === "success"
      ? styles.metricSuccess
      : tone === "warning"
        ? styles.metricWarning
        : tone === "danger"
          ? styles.metricDanger
          : tone === "accent"
            ? styles.metricAccent
            : styles.metricNeutral;
  if (!onPress) {
    return <View style={[styles.metricCard, toneStyle]}>{content}</View>;
  }
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={({ pressed }) => [styles.metricCard, toneStyle, pressed && styles.pressed]}>
      {content}
    </Pressable>
  );
}

export function EmptyState({
  icon,
  title,
  detail,
  action,
}: {
  icon?: ReactNode;
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <View style={styles.emptyState}>
      {icon}
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptyDetail}>{detail}</Text>
      {action}
    </View>
  );
}

export function SectionHeader({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return (
    <View style={styles.sectionHeader}>
      <View style={styles.sectionCopy}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {detail ? <Text style={styles.sectionDetail}>{detail}</Text> : null}
      </View>
      {action}
    </View>
  );
}

export function Panel({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[styles.panel, style]}>{children}</View>;
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.canvas,
  },
  remoteSafeArea: {
    backgroundColor: colors.remoteBg,
  },
  flex: {
    flex: 1,
  },
  scrollContent: {
    flexGrow: 1,
  },
  content: {
    flexGrow: 1,
    paddingHorizontal: spacing.screenX,
    paddingTop: 16,
    paddingBottom: spacing.bottomNav,
    gap: 16,
  },
  topBar: {
    minHeight: 62,
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 12,
  },
  topBarCopy: {
    flex: 1,
    minWidth: 0,
  },
  topBarDetail: {
    color: colors.inkMuted,
    fontSize: 14,
    lineHeight: 20,
    marginTop: 5,
  },
  remoteKicker: {
    color: colors.remoteMuted,
  },
  remoteTitle: {
    color: colors.remoteText,
  },
  remoteDetail: {
    color: colors.remoteMuted,
  },
  iconButton: {
    width: 48,
    height: 48,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  iconDanger: {
    borderColor: "#e2afba",
    backgroundColor: colors.dangerSoft,
  },
  iconRemote: {
    borderColor: colors.remoteBorder,
    backgroundColor: colors.remotePanel,
  },
  actionButton: {
    minHeight: 48,
    maxWidth: "100%",
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 14,
  },
  actionAccent: {
    backgroundColor: colors.accent,
  },
  actionSuccess: {
    backgroundColor: colors.success,
  },
  actionDanger: {
    backgroundColor: colors.danger,
  },
  actionWarning: {
    backgroundColor: colors.warning,
  },
  actionRemote: {
    backgroundColor: colors.gold,
  },
  actionNeutral: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  actionText: {
    color: "#ffffff",
    fontSize: 14,
    fontWeight: "900",
    textAlign: "center",
    flexShrink: 1,
  },
  actionTextNeutral: {
    color: colors.ink,
    fontSize: 14,
    fontWeight: "900",
    textAlign: "center",
    flexShrink: 1,
  },
  pill: {
    maxWidth: "100%",
    borderRadius: radii.sm,
    overflow: "hidden",
    paddingHorizontal: 9,
    paddingVertical: 5,
    fontSize: 12,
    fontWeight: "900",
  },
  pillNeutral: {
    backgroundColor: colors.surfaceMuted,
    color: colors.inkMuted,
  },
  pillAccent: {
    backgroundColor: colors.accentSoft,
    color: colors.accent,
  },
  pillSuccess: {
    backgroundColor: colors.successSoft,
    color: colors.success,
  },
  pillWarning: {
    backgroundColor: colors.warningSoft,
    color: colors.warning,
  },
  pillDanger: {
    backgroundColor: colors.dangerSoft,
    color: colors.danger,
  },
  notice: {
    minHeight: 48,
    borderRadius: radii.md,
    borderWidth: 1,
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 9,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  noticeNeutral: {
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  noticeSuccess: {
    borderColor: "#a6ceb7",
    backgroundColor: colors.successSoft,
  },
  noticeWarning: {
    borderColor: "#e0c676",
    backgroundColor: colors.warningSoft,
  },
  noticeDanger: {
    borderColor: "#e4aaba",
    backgroundColor: colors.dangerSoft,
  },
  noticeCopy: {
    flex: 1,
    minWidth: 0,
  },
  noticeTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "900",
  },
  noticeDetail: {
    color: colors.inkMuted,
    fontSize: 13,
    lineHeight: 19,
  },
  metricCard: {
    flex: 1,
    minWidth: 144,
    borderRadius: radii.md,
    borderWidth: 1,
    padding: 13,
    gap: 5,
    ...shadows.panel,
  },
  metricNeutral: {
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  metricAccent: {
    borderColor: "#a9c9d7",
    backgroundColor: "#eef8fb",
  },
  metricSuccess: {
    borderColor: "#a6ceb7",
    backgroundColor: colors.successSoft,
  },
  metricWarning: {
    borderColor: "#e0c676",
    backgroundColor: colors.warningSoft,
  },
  metricDanger: {
    borderColor: "#e4aaba",
    backgroundColor: colors.dangerSoft,
  },
  metricTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
  },
  metricLabel: {
    color: colors.inkSubtle,
    fontSize: 12,
    fontWeight: "900",
  },
  metricValue: {
    color: colors.ink,
    fontSize: 25,
    fontWeight: "900",
  },
  metricDetail: {
    color: colors.inkMuted,
    fontSize: 12,
    lineHeight: 17,
  },
  emptyState: {
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
    paddingVertical: 60,
    paddingHorizontal: 20,
  },
  emptyTitle: {
    color: colors.ink,
    fontSize: 20,
    fontWeight: "900",
    textAlign: "center",
  },
  emptyDetail: {
    color: colors.inkMuted,
    fontSize: 14,
    lineHeight: 21,
    textAlign: "center",
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-between",
    gap: 10,
  },
  sectionCopy: {
    flex: 1,
    minWidth: 0,
  },
  sectionTitle: {
    color: colors.ink,
    fontSize: 18,
    fontWeight: "900",
  },
  sectionDetail: {
    color: colors.inkMuted,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 3,
  },
  panel: {
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    padding: 14,
    gap: 12,
    ...shadows.panel,
  },
  disabled: {
    opacity: 0.55,
  },
  pressed: {
    opacity: 0.72,
  },
});
