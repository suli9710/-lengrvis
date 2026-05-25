import { AlertTriangle, ShieldCheck } from "lucide-react";

import type { SafetyFinding, SafetyReview, SafetySeverity } from "../../shared/types";
import { zhFindingStatus, zhSafetyStatus, zhSeverity } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface SafetyReviewPanelProps {
  review: SafetyReview;
  onOpenApproval: () => void;
}

export function SafetyReviewPanel({ review, onOpenApproval }: SafetyReviewPanelProps) {
  const openFindings = review.findings.filter((finding) => finding.status === "open").length;

  return (
    <Panel
      title="安全审核"
      eyebrow="策略检查"
      action={<Badge tone={review.status === "clear" ? "success" : "warning"}>{zhSafetyStatus(review.status)}</Badge>}
    >
      <div className="safety-summary">
        <ShieldCheck size={18} aria-hidden="true" />
        <div>
          <strong>{openFindings} 个待处理发现</strong>
          <span className="muted">更新于 {new Date(review.updatedAt).toLocaleTimeString()}</span>
        </div>
      </div>
      <div className="finding-list">
        {review.findings.map((finding) => (
          <FindingRow finding={finding} key={finding.id} />
        ))}
      </div>
      <button className="button button--secondary button--full" onClick={onOpenApproval}>
        <AlertTriangle size={16} aria-hidden="true" />
        查看审批
      </button>
    </Panel>
  );
}

function FindingRow({ finding }: { finding: SafetyFinding }) {
  return (
    <article className="finding-row">
      <div className="row row--between">
        <strong>{finding.title}</strong>
        <Badge tone={toneForSeverity(finding.severity)}>{zhSeverity(finding.severity)}</Badge>
      </div>
      <p>{finding.detail}</p>
      <span className="muted">{zhFindingStatus(finding.status)}</span>
    </article>
  );
}

function toneForSeverity(severity: SafetySeverity): "neutral" | "success" | "warning" | "danger" | "info" {
  switch (severity) {
    case "critical":
    case "high":
      return "danger";
    case "medium":
      return "warning";
    case "low":
      return "info";
    default:
      return "neutral";
  }
}
