import { useRouter } from "expo-router";
import { BellOff, ClipboardCheck, Monitor, RefreshCcw, Send, ShieldCheck, Sparkles, Timer, Unlink } from "lucide-react-native";
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { taskStarterTemplates } from "../src/taskStarterTemplates";
import type { MobileTaskMode } from "../src/api/client";
import { BottomTabs } from "../src/navigation/BottomTabs";
import { useMobileCompanion } from "../src/state/MobileCompanionContext";
import { ActionButton, EmptyState, IconButton, MetricCard, NoticeBanner, Panel, ScreenShell, SectionHeader, StatusPill, TopBar } from "../src/ui/Primitives";
import { colors, radii } from "../src/ui/theme";
import { TaskCompanionCard } from "../src/screens/CompanionCards";

const MODES: Array<{ value: MobileTaskMode; label: string }> = [
  { value: "hybrid", label: "混合" },
  { value: "privacy", label: "隐私" },
  { value: "efficiency", label: "快速" },
];

export default function HomeRoute() {
  const router = useRouter();
  const companion = useMobileCompanion();
  const selectedTemplate = taskStarterTemplates.find((template) => template.id === companion.selectedTemplateId) ?? taskStarterTemplates[0];
  const visibleTasks = companion.activeTasks.slice(0, 3);
  return (
    <>
      <ScreenShell testID="home-screen">
        <TopBar
          action={
            <View style={styles.topActions}>
              <IconButton accessibilityLabel="刷新手机 Companion" icon={<RefreshCcw size={18} color={colors.ink} />} onPress={companion.refreshAll} />
              <IconButton accessibilityLabel="断开手机连接" icon={<Unlink size={18} color={colors.danger} />} onPress={companion.disconnectPhone} tone="danger" />
            </View>
          }
          detail={companion.homeSnapshot.nextStep}
          kicker={companion.homeSnapshot.connectionLabel}
          title="手机控制台"
        />

        <View style={styles.metricGrid}>
          <MetricCard
            detail="需要你判断风险"
            icon={<ClipboardCheck size={18} color={colors.warning} />}
            label="待审批"
            onPress={() => router.replace("/approvals")}
            tone={companion.pendingCount > 0 ? "warning" : "success"}
            value={`${companion.homeSnapshot.pendingApprovals}`}
          />
          <MetricCard
            detail="可暂停、继续或补充"
            icon={<Sparkles size={18} color={colors.accent} />}
            label="电脑任务"
            tone={companion.homeSnapshot.activeTasks > 0 ? "accent" : "neutral"}
            value={`${companion.homeSnapshot.activeTasks}`}
          />
          <MetricCard
            detail={companion.homeSnapshot.remoteInputLabel}
            icon={<Monitor size={18} color={colors.success} />}
            label="远控"
            onPress={() => router.replace("/remote")}
            tone={companion.remoteInputGrant ? "success" : "neutral"}
            value={companion.remoteInputGrant ? "授权中" : "只读"}
          />
          <MetricCard
            detail="定时任务确认"
            icon={<Timer size={18} color={colors.accent} />}
            label="唤醒"
            onPress={() => router.replace("/wakeups")}
            value="查看"
          />
        </View>

        {companion.notificationsOff ? (
          <NoticeBanner
            detail="请保持手机页面打开，或定期点刷新查看新审批。"
            icon={BellOff}
            title="手机通知已关闭"
            tone="warning"
          />
        ) : null}
        {companion.error ? <NoticeBanner detail={companion.error} title="同步失败" tone="danger" /> : null}

        <Panel>
          <SectionHeader detail="从手机发起轻量任务，真正执行仍在你的电脑端。" title="任务启动器" />
          <FlatList
            contentContainerStyle={styles.templateList}
            data={taskStarterTemplates}
            horizontal
            keyExtractor={(template) => template.id}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item }) => {
              const selected = item.id === companion.selectedTemplateId;
              return (
                <Pressable
                  accessibilityLabel={`任务模板：${item.title}`}
                  accessibilityRole="button"
                  accessibilityState={{ selected }}
                  onPress={() => companion.setSelectedTemplateId(item.id)}
                  style={({ pressed }) => [styles.templateCard, selected && styles.templateSelected, pressed && styles.pressed]}
                  testID={`task-template-card-${item.id}`}
                >
                  <Text style={[styles.templateTitle, selected && styles.templateTitleSelected]}>{item.title}</Text>
                  <Text numberOfLines={2} style={styles.templateSummary}>{item.summary}</Text>
                  <StatusPill label={item.outputType} tone={selected ? "accent" : "neutral"} />
                </Pressable>
              );
            }}
            showsHorizontalScrollIndicator={false}
          />
          <TextInput
            accessibilityLabel="任务补充说明"
            multiline
            onChangeText={companion.setTaskDraft}
            placeholder={selectedTemplate.inputHint}
            placeholderTextColor={colors.inkSubtle}
            style={styles.launchInput}
            value={companion.taskDraft}
          />
          <View style={styles.launchFooter}>
            <View style={styles.modeRow}>
              {MODES.map((mode) => {
                const selected = mode.value === companion.taskMode;
                return (
                  <Pressable
                    key={mode.value}
                    accessibilityLabel={`任务模式：${mode.label}`}
                    accessibilityRole="button"
                    accessibilityState={{ selected }}
                    onPress={() => companion.setTaskMode(mode.value)}
                    style={[styles.modeButton, selected && styles.modeSelected]}
                  >
                    <Text style={[styles.modeText, selected && styles.modeTextSelected]}>{mode.label}</Text>
                  </Pressable>
                );
              })}
            </View>
            <ActionButton
              busy={companion.isStartingTask}
              icon={<Send size={16} color="#ffffff" />}
              label="发送"
              onPress={companion.submitMobileTemplateTask}
              tone="success"
            />
          </View>
        </Panel>

        <SectionHeader
          action={<ActionButton icon={<RefreshCcw size={15} color={colors.ink} />} label="刷新" onPress={companion.refreshTasks} tone="neutral" />}
          detail="只显示最近活跃任务，完整历史请回电脑端。"
          title="正在监督"
        />
        {visibleTasks.length ? (
          <View style={styles.taskList}>
            {visibleTasks.map((task) => (
              <TaskCompanionCard
                key={task.id}
                actionId={companion.taskActionId}
                followUpBusy={companion.followUpTaskId === task.id}
                followUpValue={companion.followUpDrafts[task.id] ?? ""}
                onAction={companion.submitTaskAction}
                onFollowUp={companion.submitTaskFollowUp}
                onFollowUpTextChange={(text) => companion.setFollowUpDraft(task.id, text)}
                task={task}
              />
            ))}
          </View>
        ) : (
          <EmptyState
            icon={<ShieldCheck size={34} color={colors.inkSubtle} />}
            title={companion.hasLoadedOnce ? "电脑端当前空闲" : "正在同步任务"}
            detail={companion.hasLoadedOnce ? "从上方模板发起一个任务，或等待电脑端同步新的任务进展。" : "手机正在读取电脑端的任务状态。"}
          />
        )}
      </ScreenShell>
      <BottomTabs />
    </>
  );
}

const styles = StyleSheet.create({
  topActions: {
    flexDirection: "row",
    gap: 8,
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  templateList: {
    gap: 10,
    paddingRight: 4,
  },
  templateCard: {
    width: 170,
    minHeight: 108,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceMuted,
    padding: 12,
    gap: 7,
  },
  templateSelected: {
    borderColor: colors.accent,
    backgroundColor: colors.accentSoft,
  },
  templateTitle: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "900",
  },
  templateTitleSelected: {
    color: colors.accent,
  },
  templateSummary: {
    color: colors.inkMuted,
    fontSize: 12,
    lineHeight: 17,
  },
  launchInput: {
    minHeight: 58,
    maxHeight: 96,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: "#ffffff",
    color: colors.ink,
    paddingHorizontal: 12,
    paddingVertical: 9,
    textAlignVertical: "top",
    lineHeight: 20,
  },
  launchFooter: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
  },
  modeRow: {
    flex: 1,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  modeButton: {
    minHeight: 48,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 11,
  },
  modeSelected: {
    borderColor: colors.accent,
    backgroundColor: colors.accentSoft,
  },
  modeText: {
    color: colors.inkMuted,
    fontSize: 12,
    fontWeight: "900",
  },
  modeTextSelected: {
    color: colors.accent,
  },
  taskList: {
    gap: 12,
  },
  pressed: {
    opacity: 0.72,
  },
});
