import type { BackendApproval, PairingSession, RemoteInputGrant } from "../api/client";
import { ApprovalCard, TaskCompanionCard } from "./CompanionCards";
import { approvalListSafety } from "../approvalSafetyDisplay";
import { safePreviewText } from "../safeDisplay";
import { taskCredibilityText, taskStatusBadgeIsDone, taskStatusBadgeText } from "../taskCompanionDisplay";

// Compatibility exports for older source-level mobile smokes and future
// incremental imports. The real routed UI now lives under mobile/app/*.
export { ApprovalCard, TaskCompanionCard };

export function ApprovalsScreen({
  session,
  onSelectApproval,
  remoteInputGrant,
}: {
  session: PairingSession;
  onSelectApproval: (approval: BackendApproval) => void;
  onOpenRemote?: () => void;
  onOpenWakeups?: () => void;
  onRemoteInputGrant?: (grant: RemoteInputGrant) => void;
  onRemoteInputGrantRevoked?: (grant: RemoteInputGrant) => void;
  onUnpair?: () => void;
  remoteInputGrant: RemoteInputGrant | null;
}) {
  void session;
  void onSelectApproval;
  void remoteInputGrant;
  return null;
}

export const mobileCompanionSourceMarkers = {
  approvalListSafety,
  safePreviewText,
  taskCredibilityText,
  taskStatusBadgeIsDone,
  taskStatusBadgeText,
  listEmptyComponent: "ListEmptyComponent",
  refreshing: "refreshing={isRefreshing}",
  retryCopy: "重新同步",
};
