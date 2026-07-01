import { ApprovalDialog } from "../components/ApprovalDialog";
import type { AppSurfaceProps } from "./AppSurfaceTypes";

type AppSurfaceApprovalDialogProps = Pick<
  AppSurfaceProps,
  | "pendingApproval"
  | "pendingApprovals"
  | "approvalSelectionContext"
  | "approvalQueueCursor"
  | "isApprovalOpen"
  | "approvalError"
  | "onCloseApproval"
  | "onPreviousApproval"
  | "onNextApproval"
  | "onApprovalDecision"
>;

export function AppSurfaceApprovalDialog({
  pendingApproval,
  pendingApprovals,
  approvalSelectionContext,
  approvalQueueCursor,
  isApprovalOpen,
  approvalError,
  onCloseApproval,
  onPreviousApproval,
  onNextApproval,
  onApprovalDecision
}: AppSurfaceApprovalDialogProps) {
  return (
    <ApprovalDialog
      approval={pendingApproval}
      pendingCount={pendingApprovals.length}
      selectionContext={approvalSelectionContext}
      queueIndex={pendingApproval ? pendingApprovals.findIndex((approval) => approval.id === pendingApproval.id) + 1 : 0}
      isOpen={isApprovalOpen}
      error={approvalError}
      canGoPrevious={approvalSelectionContext === "queue" && approvalQueueCursor > 0}
      canGoNext={approvalSelectionContext === "queue" && approvalQueueCursor < pendingApprovals.length - 1}
      onClose={onCloseApproval}
      onPrevious={onPreviousApproval}
      onNext={onNextApproval}
      onDecision={onApprovalDecision}
    />
  );
}
