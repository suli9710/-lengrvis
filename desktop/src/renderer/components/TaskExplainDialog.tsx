import { X } from "lucide-react";

import type {
  TaskExplain,
  TaskExplainEvidence,
  TaskResultQualityState
} from "../../shared/executionTypes";
import { sanitizeTechnicalText } from "../lib/technicalDetails";
import {
  zhAgentName,
  zhBackendTaskStatus,
  zhRiskLevel,
  zhSafetyVerdict,
  zhToolName
} from "../lib/zh";
import { AccessibleDialog } from "./AccessibleDialog";
import { Badge } from "./Panel";

interface TaskExplainDialogProps {
  explain: TaskExplain;
  taskId: string | null;
  returnFocusTo: HTMLElement | null;
  onClose: () => void;
}

export function TaskExplainDialog({
  explain,
  taskId,
  returnFocusTo,
  onClose
}: TaskExplainDialogProps) {
  const completionEvidence = explain.finalResult.completionEvidence;
  const resultQuality = explain.finalResult.resultQuality;
  return (
    <AccessibleDialog
      className="modal modal--wide"
      labelledBy="explain-title"
      returnFocusTo={returnFocusTo}
      onClose={onClose}
    >
        <header className="modal__header">
          <div>
            <span className="panel__eyebrow">执行解释</span>
            <h2 id="explain-title">为什么这样执行？</h2>
          </div>
          <div className="recording-player__header-actions">
            <Badge tone={explain.complete ? "success" : "warning"}>{explain.complete ? "完整链路" : "部分记录"}</Badge>
            <button className="icon-button" onClick={onClose} title="关闭" aria-label="关闭">
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        </header>
        <div className="modal__body">
          <div className="explain-summary">
            <div>
              <span className="muted">目标</span>
              <strong>{sanitizeTechnicalText(explain.userGoal)}</strong>
            </div>
            <div>
              <span className="muted">状态</span>
              <Badge tone={taskExplainStatusTone(explain.status)}>{zhBackendTaskStatus(explain.status)}</Badge>
            </div>
            <div>
              <span className="muted">数据来源</span>
              <span>{formatSources(explain.dataSources)}</span>
            </div>
            <div>
              <span className="muted">结果证据</span>
              <Badge tone={resultQualityTone(resultQuality.state, resultQuality.canTreatAsDone)}>
                {resultQualityLabel(resultQuality.state)}
              </Badge>
            </div>
          </div>

          <div className={`explain-result-evidence explain-result-evidence--${resultQuality.state}`}>
            <div className="row row--between">
              <strong>结果可信度</strong>
              <Badge tone={resultQualityTone(resultQuality.state, resultQuality.canTreatAsDone)}>
                {resultQuality.canTreatAsDone ? "可作为完成结果" : resultQualityLabel(resultQuality.state)}
              </Badge>
            </div>
            <p>{sanitizeTechnicalText(resultQuality.summary || completionEvidence.summary)}</p>
            {resultQuality.missingChecks.length ? (
              <ul>
                {resultQuality.missingChecks.slice(0, 4).map((missing) => (
                  <li key={missing}>{missing}</li>
                ))}
              </ul>
            ) : null}
            {resultQuality.nextStep ? <em>下一步：{resultQuality.nextStep}</em> : null}
            <span>{resultQuality.privacyNote ?? completionEvidence.privacyNote ?? "仅展示证据状态，不展示原始证据内容。"}</span>
          </div>

          <div className="explain-chain">
            {explain.chain.map((item) => (
              <article className="explain-chain__item" key={item.stage}>
                <span className="explain-chain__marker">{stageNumber(item.stage)}</span>
                <div>
                  <div className="row row--between">
                    <strong>{stageTitle(item.stage, item.title)}</strong>
                    <span className="muted">{item.evidence.length} 条证据</span>
                  </div>
                  <p>{explainStageSummary(item.stage, item.summary)}</p>
                  {item.evidence.length ? <EvidenceList evidence={item.evidence.slice(0, 3)} /> : null}
                </div>
              </article>
            ))}
          </div>

          {explain.steps.length ? (
            <div className="explain-steps">
              <strong>步骤审查</strong>
              {explain.steps.map((step) => (
                <article className="explain-step" key={step.stepId}>
                  <div className="row row--between">
                    <span>{step.order}. {zhToolName(step.toolName)}</span>
                    <Badge tone={step.requiresApproval ? "warning" : "neutral"}>{zhRiskLevel(step.riskLevel)}</Badge>
                  </div>
                  <p>{sanitizeTechnicalText(step.description)}</p>
                  {step.subagentSuggestions.map((message) => (
                    <p className="muted" key={message.id}>
                      {zhAgentName(message.fromAgent)}：{sanitizeTechnicalText(message.content)}
                    </p>
                  ))}
                  {step.safetyReviews.map((review) => (
                    <p className="muted" key={review.id}>
                      安全审查 {zhSafetyVerdict(review.verdict)}：{sanitizeTechnicalText(review.reasons.join(" "))}
                    </p>
                  ))}
                </article>
              ))}
            </div>
          ) : null}
        </div>
        <footer className="modal__footer">
          <span className="muted">{taskId ? "证据链已脱敏" : "无关联任务"}</span>
          <button className="button button--ghost" onClick={onClose}>
            <X size={14} aria-hidden="true" />
            关闭
          </button>
        </footer>
    </AccessibleDialog>
  );
}

export function taskExplainStatusTone(
  status: string
): "neutral" | "success" | "warning" | "danger" | "info" {
  switch (status.trim().toLowerCase()) {
    case "completed":
      return "success";
    case "failed":
    case "repair_required":
    case "denied":
    case "cancelled":
      return "danger";
    case "blocked":
    case "paused":
    case "awaiting_approval":
    case "waiting_user_approval":
    case "rolled_back":
      return "warning";
    case "running":
    case "goal_analysis":
    case "planning":
    case "consultation":
    case "plan_review":
    case "execution":
    case "final_review":
      return "info";
    default:
      return "neutral";
  }
}

function EvidenceList({ evidence }: { evidence: TaskExplainEvidence[] }) {
  return (
    <ul className="explain-evidence">
      {evidence.map((item) => (
        <li key={`${item.source}-${item.id}`}>
          <span>{item.source}</span>
          <p>{item.actor ? `${zhAgentName(item.actor)}：` : ""}{sanitizeTechnicalText(item.summary)}</p>
        </li>
      ))}
    </ul>
  );
}

function explainStageSummary(stage: string, summary: string): string {
  if (stage === "planner_reasoning") {
    return "已记录计划选择；为保护隐私与可读性，不展示模型内部推理过程。";
  }
  return sanitizeTechnicalText(summary);
}

function stageTitle(stage: string, fallback: string) {
  const labels: Record<string, string> = {
    user_goal: "用户目标",
    supervisor_judgment: "主管判断",
    planner_reasoning: "计划依据",
    step_safety_reviews: "每步安全审查",
    subagent_suggestions: "子 Agent 建议",
    final_result: "最终结果"
  };
  return labels[stage] ?? fallback;
}

function stageNumber(stage: string) {
  const order = ["user_goal", "supervisor_judgment", "planner_reasoning", "step_safety_reviews", "subagent_suggestions", "final_result"];
  const index = order.indexOf(stage);
  return index >= 0 ? index + 1 : "·";
}

function formatSources(sources: Record<string, number>) {
  return Object.entries(sources)
    .map(([name, count]) => `${name}: ${count}`)
    .join(" / ");
}

function resultQualityLabel(state: TaskResultQualityState): string {
  const labels: Record<TaskResultQualityState, string> = {
    verified_result: "完成结果已核验",
    visible_progress: "有进度待核验",
    safe_failure: "安全停止",
    task_evidence_only: "仅有任务记录"
  };
  return labels[state];
}

function resultQualityTone(
  state: TaskResultQualityState,
  canTreatAsDone: boolean
): "neutral" | "success" | "warning" | "danger" | "info" {
  if (state === "verified_result" && canTreatAsDone) return "success";
  if (state === "safe_failure") return "danger";
  if (state === "visible_progress") return "info";
  if (state === "task_evidence_only") return "warning";
  return "neutral";
}
