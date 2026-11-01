import { lazy, Suspense, useEffect, useState } from "react";

import { PlanViewer } from "../../components/PlanViewer";
import { RouteLoading } from "../../appViewModel";
import { useUiPreferences } from "../../lib/uiPreferences";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const AgentConversationPanel = lazy(() => import("../../components/AgentConversationPanel").then((module) => ({ default: module.AgentConversationPanel })));
const ArtifactsPanel = lazy(() => import("../../components/ArtifactsPanel").then((module) => ({ default: module.ArtifactsPanel })));
const MetricsPanel = lazy(() => import("../../components/MetricsPanel").then((module) => ({ default: module.MetricsPanel })));
const SchedulePanel = lazy(() => import("../../components/SchedulePanel").then((module) => ({ default: module.SchedulePanel })));
const TaskTimeline = lazy(() => import("../../components/TaskTimeline").then((module) => ({ default: module.TaskTimeline })));

type AgentsRouteProps = Pick<
  AppSurfaceProps,
  "agentConversations" | "api" | "focusedTaskId" | "plan" | "tasks" | "onRevealPath" | "onTaskPilotAction"
>;

export function AgentsRoute({
  agentConversations,
  api,
  focusedTaskId,
  onTaskPilotAction,
  plan,
  tasks,
  onRevealPath
}: AgentsRouteProps) {
  const { preferences } = useUiPreferences();
  const [isMoreOpen, setIsMoreOpen] = useState(preferences.detailMode === "expert");

  useEffect(() => {
    setIsMoreOpen(preferences.detailMode === "expert");
  }, [preferences.detailMode]);

  return (
    <section className="detail-grid">
      <PlanViewer plan={plan} />
      <Suspense fallback={<RouteLoading />}>
        <TaskTimeline tasks={tasks} api={api} focusedTaskId={focusedTaskId} onTaskPilotAction={onTaskPilotAction} />
        <ArtifactsPanel tasks={tasks} api={api} focusedTaskId={focusedTaskId} onRevealPath={onRevealPath} />
      </Suspense>
      <details
        className="progress-more detail-grid__full"
        data-testid="progress-more"
        open={isMoreOpen}
        onToggle={(event) => setIsMoreOpen(event.currentTarget.open)}
      >
        <summary className="progress-more__summary">
          <span>更多：协作记录、定时任务与本机指标</span>
          <em>技术详情按需查看，任务进展与成果始终可见</em>
        </summary>
        <div className="progress-more__panels">
          <Suspense fallback={<RouteLoading />}>
            <AgentConversationPanel conversations={agentConversations} />
            <SchedulePanel api={api} />
            <MetricsPanel api={api} />
          </Suspense>
        </div>
      </details>
    </section>
  );
}
