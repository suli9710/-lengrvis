import { lazy, Suspense } from "react";

import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const SettingsPanel = lazy(() => import("../../components/SettingsPanel").then((module) => ({ default: module.SettingsPanel })));

type SettingsRouteProps = Pick<
  AppSurfaceProps,
  | "api"
  | "backendStatus"
  | "llmCostSummary"
  | "llmHealth"
  | "localLlmHealth"
  | "realtimeStatus"
  | "settings"
  | "settingsIntent"
  | "onLocalLlmHealthChange"
  | "onSaveSettings"
  | "onStartBackend"
  | "onStopBackend"
>;

export function SettingsRoute({
  api,
  backendStatus,
  llmCostSummary,
  llmHealth,
  localLlmHealth,
  realtimeStatus,
  settings,
  settingsIntent,
  onLocalLlmHealthChange,
  onSaveSettings,
  onStartBackend,
  onStopBackend
}: SettingsRouteProps) {
  return (
    <section className="detail-grid detail-grid--settings">
      <Suspense fallback={<RouteLoading />}>
        <SettingsPanel
          settings={settings}
          backendStatus={backendStatus}
          realtimeStatus={realtimeStatus}
          localLlmHealth={localLlmHealth}
          llmHealth={llmHealth}
          llmCostSummary={llmCostSummary}
          onLocalLlmHealthChange={onLocalLlmHealthChange}
          onSave={onSaveSettings}
          onStartBackend={onStartBackend}
          onStopBackend={onStopBackend}
          api={api}
          privacyIntentId={settingsIntent?.section === "privacy" ? settingsIntent.nonce : undefined}
        />
      </Suspense>
    </section>
  );
}
