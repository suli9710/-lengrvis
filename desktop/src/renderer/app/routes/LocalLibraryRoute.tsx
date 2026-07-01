import { lazy, Suspense } from "react";

import { RouteLoading } from "../../appViewModel";
import { sectionForView } from "../../views/localLibrarySections";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const LocalLibraryView = lazy(() => import("../../views/LocalLibraryView").then((module) => ({ default: module.LocalLibraryView })));

type LocalLibraryRouteProps = Pick<AppSurfaceProps, "activeView" | "api" | "onUseDocument" | "onViewChange">;

export function LocalLibraryRoute({
  activeView,
  api,
  onUseDocument,
  onViewChange
}: LocalLibraryRouteProps) {
  return (
    <Suspense fallback={<RouteLoading />}>
      <LocalLibraryView
        api={api}
        activeSection={sectionForView(activeView)}
        onSectionChange={(section) => onViewChange(section)}
        onUseDocument={onUseDocument}
      />
    </Suspense>
  );
}
