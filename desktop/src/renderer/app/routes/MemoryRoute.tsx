import { lazy, Suspense } from "react";

import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const MemoryPanel = lazy(() => import("../../components/MemoryPanel").then((module) => ({ default: module.MemoryPanel })));

type MemoryRouteProps = Pick<AppSurfaceProps, "api">;

export function MemoryRoute({ api }: MemoryRouteProps) {
  return (
    <section className="detail-grid">
      <Suspense fallback={<RouteLoading />}>
        <MemoryPanel api={api} />
      </Suspense>
    </section>
  );
}
