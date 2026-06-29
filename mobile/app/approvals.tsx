import { BellOff, ClipboardCheck, RefreshCcw, ShieldCheck } from "lucide-react-native";
import { FlatList, StyleSheet } from "react-native";

import { BottomTabs } from "../src/navigation/BottomTabs";
import { ApprovalCard } from "../src/screens/CompanionCards";
import { useMobileCompanion } from "../src/state/MobileCompanionContext";
import { ActionButton, EmptyState, NoticeBanner, ScreenShell, SectionHeader, TopBar } from "../src/ui/Primitives";
import { colors } from "../src/ui/theme";

export default function ApprovalsRoute() {
  const companion = useMobileCompanion();
  const title = companion.pendingCount === 0 ? "审批队列" : `${companion.pendingCount} 项待审批`;
  return (
    <>
      <ScreenShell scroll={false} testID="approvals-screen">
        <TopBar
          action={<ActionButton busy={companion.isRefreshing} icon={<RefreshCcw size={15} color={colors.ink} />} label="刷新" onPress={companion.refreshAll} tone="neutral" />}
          detail="手机端只做监督和确认，高风险或看不懂的动作应回电脑端处理。"
          kicker={companion.homeSnapshot.connectionLabel}
          title={title}
        />
        {companion.notificationsOff ? (
          <NoticeBanner detail="通知关闭时，请保持此页打开或定期刷新。" icon={BellOff} tone="warning" />
        ) : null}
        {companion.error ? <NoticeBanner detail={companion.error} title="同步失败" tone="danger" /> : null}
        <SectionHeader detail="列表只展示审批，任务监督已移到首页。" title="请求" />
        <FlatList
          contentContainerStyle={companion.approvals.length ? styles.list : styles.emptyList}
          data={companion.approvals}
          keyExtractor={(approval) => approval.id}
          keyboardDismissMode="on-drag"
          keyboardShouldPersistTaps="handled"
          ListEmptyComponent={
            <EmptyState
              icon={companion.hasLoadedOnce ? <ShieldCheck size={36} color={colors.success} /> : <ClipboardCheck size={36} color={colors.inkSubtle} />}
              title={companion.hasLoadedOnce ? "暂无待处理审批" : "正在同步审批"}
              detail={companion.hasLoadedOnce ? "有新的写入、远控或高风险动作时会出现在这里。" : "手机正在连接电脑端审批流。"}
            />
          }
          onRefresh={companion.refreshAll}
          refreshing={companion.isRefreshing}
          renderItem={({ item }) => (
            <ApprovalCard
              approval={item}
              onPress={() => companion.onSelectApproval(item)}
              remoteInputGrant={companion.remoteInputGrant}
              session={companion.session}
            />
          )}
        />
      </ScreenShell>
      <BottomTabs />
    </>
  );
}

const styles = StyleSheet.create({
  list: {
    gap: 12,
    paddingBottom: 118,
  },
  emptyList: {
    flexGrow: 1,
    paddingBottom: 118,
  },
});
