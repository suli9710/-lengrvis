import { CheckCircle2, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ApprovalRequest } from "../../shared/types";
import { zhAgentName, zhSeverity } from "../lib/zh";
import { Badge } from "./Panel";

interface ApprovalDialogProps {
  approval: ApprovalRequest | null;
  isOpen: boolean;
  error?: string | null;
  onClose: () => void;
  onDecision: (approvalId: string, decision: "approved" | "denied", note?: string) => Promise<void>;
}

export function ApprovalDialog({ approval, isOpen, error, onClose, onDecision }: ApprovalDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [note, setNote] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (isOpen) {
      dialogRef.current?.focus();
    }
  }, [isOpen]);

  if (!isOpen || !approval) {
    return null;
  }

  const decide = async (decision: "approved" | "denied") => {
    setIsSubmitting(true);
    try {
      await onDecision(approval.id, decision, note.trim() || undefined);
      setNote("");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-title"
        tabIndex={-1}
        ref={dialogRef}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            onClose();
          }
        }}
      >
        <header className="modal__header">
          <div>
            <span className="panel__eyebrow">审批</span>
            <h2 id="approval-title">{approval.title}</h2>
          </div>
          <Badge tone={approval.riskLevel === "high" || approval.riskLevel === "critical" ? "danger" : "warning"}>
            {zhSeverity(approval.riskLevel)}
          </Badge>
        </header>
        <div className="modal__body">
          <dl className="detail-list">
            <div>
              <dt>请求方</dt>
              <dd>{zhAgentName(approval.requester)}</dd>
            </div>
            <div>
              <dt>原因</dt>
              <dd>{approval.reason}</dd>
            </div>
            <div>
              <dt>动作</dt>
              <dd>{approval.proposedAction}</dd>
            </div>
          </dl>
          <label className="field">
            <span>审批备注</span>
            <textarea value={note} onChange={(event) => setNote(event.target.value)} rows={4} />
            {error ? <p className="field-error" role="alert">{error}</p> : null}
          </label>
        </div>
        <footer className="modal__footer">
          <button className="button button--ghost" onClick={onClose} disabled={isSubmitting}>
            取消
          </button>
          <button className="button button--danger" onClick={() => void decide("denied")} disabled={isSubmitting}>
            <XCircle size={16} aria-hidden="true" />
            拒绝
          </button>
          <button className="button button--primary" onClick={() => void decide("approved")} disabled={isSubmitting}>
            <CheckCircle2 size={16} aria-hidden="true" />
            批准
          </button>
        </footer>
      </div>
    </div>
  );
}
