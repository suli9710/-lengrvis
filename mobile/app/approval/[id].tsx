import { useLocalSearchParams, useRouter } from "expo-router";
import { useMemo } from "react";

import { ApprovalDetail } from "../../src/screens/ApprovalDetail";
import { useMobileCompanion } from "../../src/state/MobileCompanionContext";
import { EmptyState, ScreenShell } from "../../src/ui/Primitives";

export default function ApprovalDetailRoute() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string }>();
  const companion = useMobileCompanion();
  const approval = useMemo(
    () => companion.approvals.find((item) => item.id === params.id) ?? null,
    [companion.approvals, params.id],
  );
  if (!approval) {
    return (
      <ScreenShell>
        <EmptyState title="审批已不在列表中" detail="它可能已经被处理。返回审批页刷新即可看到最新状态。" />
      </ScreenShell>
    );
  }
  return (
    <ApprovalDetail
      approval={approval}
      onBack={() => router.back()}
      onSessionExpired={companion.onSessionExpired}
      onUpdated={companion.updateApproval}
      remoteInputGrant={companion.remoteInputGrant}
      session={companion.session}
    />
  );
}
