import { ClipboardList } from "lucide-react";

import type { Plan, PlanStepState } from "../../shared/types";
import { zhAgentName, zhStepState } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface PlanViewerProps {
  plan: Plan;
}

export function PlanViewer({ plan }: PlanViewerProps) {
  return (
    <Panel
      title="执行计划"
      eyebrow="当前目标"
      action={<Badge tone="info">更新于 {new Date(plan.updatedAt).toLocaleTimeString()}</Badge>}
    >
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
              <span className="muted">{zhAgentName(step.owner)}</span>
            </div>
          </article>
        ))}
      </div>
    </Panel>
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
