import { lazy, Suspense } from "react";

import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const BrowserActivityPanel = lazy(() => import("../../components/BrowserActivityPanel").then((module) => ({ default: module.BrowserActivityPanel })));

type BrowserRouteProps = Pick<
  AppSurfaceProps,
  | "activeBrowserSessionId"
  | "api"
  | "browserError"
  | "browserEvents"
  | "browserHostSnapshot"
  | "browserSessions"
  | "onActiveBrowserSessionChange"
  | "onBrowserErrorChange"
  | "onBrowserEventsChange"
  | "onBrowserHostSnapshotChange"
  | "onBrowserSessionsChange"
>;

export function BrowserRoute({
  activeBrowserSessionId,
  api,
  browserError,
  browserEvents,
  browserHostSnapshot,
  browserSessions,
  onActiveBrowserSessionChange,
  onBrowserErrorChange,
  onBrowserEventsChange,
  onBrowserHostSnapshotChange,
  onBrowserSessionsChange
}: BrowserRouteProps) {
  return (
    <section className="browser-view">
      <Suspense fallback={<RouteLoading />}>
        <BrowserActivityPanel
          api={api}
          sessions={browserSessions}
          events={browserEvents}
          hostSnapshot={browserHostSnapshot}
          activeSessionId={activeBrowserSessionId}
          error={browserError}
          onSessionsChange={onBrowserSessionsChange}
          onEventsChange={onBrowserEventsChange}
          onHostSnapshotChange={onBrowserHostSnapshotChange}
          onActiveSessionChange={onActiveBrowserSessionChange}
          onErrorChange={onBrowserErrorChange}
        />
      </Suspense>
    </section>
  );
}
