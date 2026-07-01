import { lazy, Suspense } from "react";

import { officeAgents } from "../../features/office";
import { quickSkills } from "../../features/office/quickSkills";
import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const OfficeScene = lazy(() => import("../../features/office/OfficeScene").then((module) => ({ default: module.OfficeScene })));

type HomeRouteProps = Pick<
  AppSurfaceProps,
  | "connectionState"
  | "draft"
  | "heroSubmitError"
  | "heroSubmitting"
  | "homeReadinessItems"
  | "homeTrustItems"
  | "pendingApprovals"
  | "tasks"
  | "onDraftChange"
  | "onQuickSkill"
  | "onReadinessAction"
  | "onSubmitHeroPrompt"
  | "onTaskPilotAction"
> & {
  activeOfficeAgentId: string;
  safetyAlert: boolean;
};

export function HomeRoute({
  activeOfficeAgentId,
  connectionState,
  draft,
  heroSubmitError,
  heroSubmitting,
  homeReadinessItems,
  homeTrustItems,
  pendingApprovals,
  safetyAlert,
  tasks,
  onDraftChange,
  onQuickSkill,
  onReadinessAction,
  onSubmitHeroPrompt,
  onTaskPilotAction
}: HomeRouteProps) {
  return (
    <section className="lengrvis-home">
      <Suspense fallback={<RouteLoading />}>
        <OfficeScene
          agents={officeAgents}
          draft={draft}
          onDraftChange={onDraftChange}
          onSubmitPrompt={onSubmitHeroPrompt}
          onAgentSelect={(prompt) => onDraftChange(prompt)}
          connectionState={connectionState}
          isSubmitting={heroSubmitting}
          submitError={heroSubmitError}
          activeAgentId={activeOfficeAgentId}
          recentTasks={tasks}
          quickSkills={quickSkills}
          readinessItems={homeReadinessItems}
          trustItems={homeTrustItems}
          onQuickSkill={onQuickSkill}
          onReadinessAction={onReadinessAction}
          onTaskPilotAction={onTaskPilotAction}
          pendingApprovalCount={pendingApprovals.length}
          safetyAlert={safetyAlert}
        />
      </Suspense>
    </section>
  );
}
