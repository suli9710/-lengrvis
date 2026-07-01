import { lazy, Suspense } from "react";

import { PlanViewer } from "../../components/PlanViewer";
import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const SystemInfoPanel = lazy(() => import("../../components/SystemInfoPanel").then((module) => ({ default: module.SystemInfoPanel })));

type ComputerRouteProps = Pick<
  AppSurfaceProps,
  | "backendStatus"
  | "isCheckingComputer"
  | "plan"
  | "systemInfo"
  | "onExportDiagnostics"
  | "onOpenWindowsSettings"
  | "onRefreshSystemInfo"
  | "onRevealPath"
>;

export function ComputerRoute({
  backendStatus,
  isCheckingComputer,
  plan,
  systemInfo,
  onExportDiagnostics,
  onOpenWindowsSettings,
  onRefreshSystemInfo,
  onRevealPath
}: ComputerRouteProps) {
  return (
    <section className="detail-grid">
      <Suspense fallback={<RouteLoading />}>
        <SystemInfoPanel
          info={systemInfo}
          backendStatus={backendStatus}
          isRefreshing={isCheckingComputer}
          onRefresh={onRefreshSystemInfo}
          onExportDiagnostics={onExportDiagnostics}
          onRevealPath={onRevealPath}
          onOpenSettings={onOpenWindowsSettings}
        />
      </Suspense>
      <PlanViewer plan={plan} />
    </section>
  );
}
