import { CheckCircle2, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ApprovalRequest, CleanupPlan } from "../../shared/types";
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

  const cleanupPlan = approval.cleanupPlan;
  const cleanupGroups = splitCleanupItems(cleanupPlan);

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
          {cleanupPlan ? (
            <CleanupApprovalPreview
              plan={cleanupPlan}
              permanent={cleanupGroups.permanent}
              trash={cleanupGroups.trash}
              suggestions={cleanupGroups.suggestions}
            />
          ) : null}
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

function CleanupApprovalPreview({
  plan,
  permanent,
  trash,
  suggestions
}: {
  plan: CleanupPlan;
  permanent: CleanupPlan["items"];
  trash: CleanupPlan["items"];
  suggestions: CleanupPlan["items"];
}) {
  return (
    <section className="approval-cleanup">
      <div className="approval-cleanup__summary">
        <div>
          <span>预计释放</span>
          <strong>{formatBytes(plan.reclaimableBytes)}</strong>
        </div>
        <div>
          <span>永久删除</span>
          <strong>{permanent.length} 项</strong>
        </div>
        <div>
          <span>进回收站</span>
          <strong>{trash.length} 项</strong>
        </div>
      </div>
      <CleanupApprovalBucket
        title="永久删除"
        description="批准后会直接删除，通常不可从回收站恢复。"
        tone="danger"
        items={permanent}
        emptyText="没有永久删除项"
      />
      <CleanupApprovalBucket
        title="进回收站"
        description="批准后会移入回收站，仍可能手动恢复。"
        tone="warning"
        items={trash}
        emptyText="没有回收站项"
      />
      <CleanupApprovalBucket
        title="仅建议"
        description="这些项目只提示你检查，不会随本次批准删除。"
        tone="neutral"
        items={suggestions}
        emptyText="没有仅建议项"
      />
      {plan.riskWarnings.length ? (
        <div className="approval-cleanup__risks">
          <strong>风险提示</strong>
          <ul>
            {plan.riskWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function CleanupApprovalBucket({
  title,
  description,
  tone,
  items,
  emptyText
}: {
  title: string;
  description: string;
  tone: "neutral" | "warning" | "danger";
  items: CleanupPlan["items"];
  emptyText: string;
}) {
  return (
    <section className="approval-cleanup__bucket">
      <div className="row row--between">
        <div>
          <strong>{title}</strong>
          <p className="muted">{description}</p>
        </div>
        <Badge tone={tone}>{items.length} 项</Badge>
      </div>
      {items.length ? (
        <ul>
          {items.slice(0, 6).map((item) => (
            <li key={item.id}>
              <span>{item.path}</span>
              <em>{formatBytes(item.sizeBytes ?? (item.sizeMb ? item.sizeMb * 1024 * 1024 : undefined))}</em>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{emptyText}</p>
      )}
    </section>
  );
}

function splitCleanupItems(plan?: CleanupPlan) {
  const items = plan?.items ?? [];
  return {
    permanent: items.filter((item) => item.disposition === "permanent_delete"),
    trash: items.filter((item) => item.disposition === "trash"),
    suggestions: items.filter((item) => item.disposition !== "permanent_delete" && item.disposition !== "trash")
  };
}

function formatBytes(bytes?: number): string {
  if (!bytes || !Number.isFinite(bytes)) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}
