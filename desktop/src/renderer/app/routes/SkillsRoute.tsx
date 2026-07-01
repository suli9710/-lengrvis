import { lazy, Suspense } from "react";

import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const SkillsView = lazy(() => import("../../views/SkillsView").then((module) => ({ default: module.SkillsView })));

type SkillsRouteProps = Pick<AppSurfaceProps, "api">;

export function SkillsRoute({ api }: SkillsRouteProps) {
  return (
    <section className="detail-grid detail-grid--settings">
      <Suspense fallback={<RouteLoading />}>
        <SkillsView api={api} />
      </Suspense>
    </section>
  );
}
