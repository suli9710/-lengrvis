import { lazy, Suspense } from "react";

import { PlanViewer } from "../../components/PlanViewer";
import { RouteLoading } from "../../appViewModel";
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
  return (
    <section className="detail-grid">
      <Suspense fallback={<RouteLoading />}>
        <AgentConversationPanel conversations={agentConversations} />
        <TaskTimeline tasks={tasks} api={api} focusedTaskId={focusedTaskId} onTaskPilotAction={onTaskPilotAction} />
      </Suspense>
      <PlanViewer plan={plan} />
      <details className="progress-more detail-grid__full" data-testid="progress-more">
        <summary className="progress-more__summary">
          <span>更多：定时任务、成果产物与本机指标</span>
          <em>需要时再展开，默认聚焦当前进展</em>
        </summary>
        <div className="progress-more__panels">
          <Suspense fallback={<RouteLoading />}>
            <SchedulePanel api={api} />
            <ArtifactsPanel tasks={tasks} api={api} focusedTaskId={focusedTaskId} onRevealPath={onRevealPath} />
            <MetricsPanel api={api} />
          </Suspense>
        </div>
      </details>
    </section>
  );
}
