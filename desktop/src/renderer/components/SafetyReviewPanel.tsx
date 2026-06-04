import { AlertTriangle, ShieldCheck } from "lucide-react";

import type { SafetyFinding, SafetyReview, SafetySeverity, TaskBoundaryEvent } from "../../shared/types";
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
      {review.boundaryEvents?.length ? (
        <div className="finding-list">
          {review.boundaryEvents.slice(-4).map((event) => (
            <BoundaryFinding event={event} key={event.id} />
          ))}
        </div>
      ) : null}
      <button className="button button--secondary button--full" onClick={onOpenApproval}>
        <AlertTriangle size={16} aria-hidden="true" />
        查看审批
      </button>
    </Panel>
  );
}

function BoundaryFinding({ event }: { event: TaskBoundaryEvent }) {
  return (
    <article className="finding-row finding-row--boundary">
      <div className="row row--between">
        <strong>{event.title}</strong>
        <Badge tone={toneForBoundary(event.severity)}>{boundaryKindLabel(event.kind)}</Badge>
      </div>
      <p>{event.detail}</p>
      <span className="muted">{new Date(event.createdAt).toLocaleTimeString()}</span>
    </article>
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

function boundaryKindLabel(kind: string) {
  if (kind === "model_boundary_denied") return "模型边界";
  if (kind === "context_boundary" || kind === "context_projection") return "上下文";
  if (kind === "tool_progress") return "工具进度";
  if (kind === "post_tool_review") return "工具审查";
  if (kind === "tool_contract") return "工具契约";
  return "边界";
}

function toneForBoundary(severity: string): "neutral" | "success" | "warning" | "danger" | "info" {
  if (severity === "danger" || severity === "critical" || severity === "high") return "danger";
  if (severity === "warning" || severity === "medium") return "warning";
  if (severity === "success" || severity === "low") return "success";
  return "info";
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
