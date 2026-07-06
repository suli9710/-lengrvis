import { useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useMemo, useState } from "react";

import { getApprovalDetail, type BackendApproval } from "../../src/api/client";
import { ApprovalDetail } from "../../src/screens/ApprovalDetail";
import { useMobileCompanion } from "../../src/state/MobileCompanionContext";
import { EmptyState, ScreenShell } from "../../src/ui/Primitives";

export default function ApprovalDetailRoute() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string }>();
  const approvalId = typeof params.id === "string" ? params.id : undefined;
  const companion = useMobileCompanion();
  const {
    approvals,
    onSessionExpired,
    remoteInputGrant,
    session,
    updateApproval,
  } = companion;
  const localApproval = useMemo(
    () => approvals.find((item) => item.id === approvalId) ?? null,
    [approvalId, approvals],
  );
  const [fetchedApproval, setFetchedApproval] = useState<BackendApproval | null>(null);
  const [missingFetchState, setMissingFetchState] = useState<"idle" | "loading" | "failed">("idle");
  const approval = localApproval ?? (fetchedApproval?.id === approvalId ? fetchedApproval : null);
  const shouldFetchMissingApproval = Boolean(approvalId && !approval && session);

  useEffect(() => {
    if (!shouldFetchMissingApproval || !approvalId) {
      setMissingFetchState("idle");
      return undefined;
    }
    let isActive = true;
    setMissingFetchState("loading");
    void getApprovalDetail(session, approvalId)
      .then((detail) => {
        if (!isActive) return;
        setFetchedApproval(detail.approval);
        updateApproval(detail.approval);
        setMissingFetchState("idle");
      })
      .catch(() => {
        if (!isActive) return;
        setFetchedApproval(null);
        setMissingFetchState("failed");
      });
    return () => {
      isActive = false;
    };
  }, [approvalId, session, shouldFetchMissingApproval, updateApproval]);

  if (!approval && (missingFetchState === "loading" || (missingFetchState === "idle" && shouldFetchMissingApproval))) {
    return (
      <ScreenShell>
        <EmptyState title="正在同步审批" detail="正在从电脑拉取这条审批详情。" />
      </ScreenShell>
    );
  }

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
      onSessionExpired={onSessionExpired}
      onUpdated={updateApproval}
      remoteInputGrant={remoteInputGrant}
      session={session}
    />
  );
}
