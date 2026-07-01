import { lazy, Suspense } from "react";

import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const AuditLogPanel = lazy(() => import("../../components/AuditLogPanel").then((module) => ({ default: module.AuditLogPanel })));
const SafetyReviewPanel = lazy(() => import("../../components/SafetyReviewPanel").then((module) => ({ default: module.SafetyReviewPanel })));

type SafetyRouteProps = Pick<AppSurfaceProps, "auditEntries" | "safetyReview" | "onOpenTaskApproval">;

export function SafetyRoute({
  auditEntries,
  safetyReview,
  onOpenTaskApproval
}: SafetyRouteProps) {
  return (
    <section className="detail-grid">
      <Suspense fallback={<RouteLoading />}>
        <SafetyReviewPanel review={safetyReview} onOpenApproval={onOpenTaskApproval} />
        <AuditLogPanel entries={auditEntries} />
      </Suspense>
    </section>
  );
}
