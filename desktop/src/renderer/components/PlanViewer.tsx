import { ClipboardList } from "lucide-react";

import type { Plan, PlanStepState } from "../../shared/executionTypes";
import { zhAgentName, zhStepState } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface PlanViewerProps {
  plan: Plan;
}

export function PlanViewer({ plan }: PlanViewerProps) {
  const hasSteps = plan.steps.length > 0;

  return (
    <Panel
      title="执行计划"
      eyebrow="当前目标"
      action={
        hasSteps
          ? <Badge tone="info">更新于 {new Date(plan.updatedAt).toLocaleTimeString()}</Badge>
          : <Badge tone="neutral">暂无计划</Badge>
      }
    >
      {hasSteps ? (
        <>
          <div className="plan-summary">
            <ClipboardList size={18} aria-hidden="true" />
            <div>
              <strong>{plan.title}</strong>
              <p>{plan.objective}</p>
            </div>
          </div>
          <div className="step-list">
            {plan.steps.map((step, index) => (
              <article className="step-row" key={step.id}>
                <span className={`step-row__index step-row__index--${step.state}`}>{index + 1}</span>
                <div>
                  <div className="row row--between">
                    <strong>{step.title}</strong>
                    <Badge tone={toneForStep(step.state)}>{zhStepState(step.state)}</Badge>
                  </div>
                  <p>{step.detail}</p>
                  <StepBoundarySummary step={step} />
                  <span className="muted">{zhAgentName(step.owner)}</span>
                </div>
              </article>
            ))}
          </div>
        </>
      ) : (
        <div className="empty-state">
          <ClipboardList size={18} aria-hidden="true" />
          <span>暂无执行计划。真实任务生成计划后会显示步骤和负责 Agent。</span>
        </div>
      )}
    </Panel>
  );
}

function StepBoundarySummary({ step }: { step: Plan["steps"][number] }) {
  const chips: Array<{ key: string; label: string; tone: "neutral" | "success" | "warning" | "danger" | "info" }> = [];
  if (step.toolName) chips.push({ key: "tool", label: step.toolName, tone: "info" });
  if (step.riskLevel) chips.push({ key: "risk", label: `risk: ${step.riskLevel}`, tone: toneForRisk(step.riskLevel) });
  if (step.trustTier) chips.push({ key: "trust", label: `trust: ${step.trustTier}`, tone: "neutral" });
  if (step.approvalState) chips.push({ key: "approval", label: `approval: ${step.approvalState}`, tone: step.approvalState === "required" ? "warning" : "success" });
  if (step.deferredTool) chips.push({ key: "deferred", label: "deferred search", tone: "info" });
  for (const effect of step.effects ?? []) {
    chips.push({ key: `effect-${effect}`, label: `effect: ${effect}`, tone: "neutral" });
  }
  for (const resource of step.resourceKinds ?? []) {
    chips.push({ key: `resource-${resource}`, label: `resource: ${resource}`, tone: "neutral" });
  }
  if (!chips.length) return null;
  return (
    <div className="step-row__boundary" aria-label="步骤工程边界">
      {chips.map((chip) => <Badge key={chip.key} tone={chip.tone}>{chip.label}</Badge>)}
    </div>
  );
}

function toneForStep(state: PlanStepState): "neutral" | "success" | "warning" | "danger" | "info" {
  switch (state) {
    case "done":
      return "success";
    case "active":
      return "info";
    case "blocked":
      return "warning";
    default:
      return "neutral";
  }
}

function toneForRisk(risk: string): "neutral" | "success" | "warning" | "danger" | "info" {
  const normalized = risk.toLowerCase();
  if (normalized.includes("r3") || normalized.includes("critical") || normalized.includes("destructive")) return "danger";
  if (normalized.includes("r2") || normalized.includes("high") || normalized.includes("modify")) return "warning";
  if (normalized.includes("r1") || normalized.includes("medium")) return "info";
  return "success";
}
