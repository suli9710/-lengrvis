import type {
  DiagnosticExportResult,
  LocalMetricsSummary,
  StartupItem,
  SystemDiagnostic,
  SystemProcess
} from "../../../shared/systemTypes";
import type {
  BackendDiagnosticExportResult,
  BackendLocalMetrics,
  BackendProcess,
  BackendStartupItem,
  BackendSupportPackageRedaction,
  BackendSystemDiagnostics
} from "./systemBackendTypes";
import { zhBackendText } from "../zh";

export function mapLocalMetrics(data: BackendLocalMetrics, fallbackWindowDays = 7): LocalMetricsSummary {
  return {
    windowDays: Number(data.window_days ?? fallbackWindowDays),
    generatedAt: data.generated_at ?? "",
    tasks: {
      total: Number(data.tasks?.total ?? 0),
      terminal: Number(data.tasks?.terminal ?? 0),
      succeeded: Number(data.tasks?.succeeded ?? 0),
      successRate: data.tasks?.success_rate ?? null,
      byStatus: data.tasks?.by_status ?? {}
    },
    runs: {
      total: Number(data.runs?.total ?? 0),
      byPhase: data.runs?.by_phase ?? {}
    },
    recovery: {
      reflectionsStarted: Number(data.recovery?.reflections_started ?? 0),
      runsWithReflection: Number(data.recovery?.runs_with_reflection ?? 0),
      recoveryTriggerRate: data.recovery?.recovery_trigger_rate ?? null,
      decidedActions: data.recovery?.decided_actions ?? {},
      askUserShare: data.recovery?.ask_user_share ?? null
    },
    llm: {
      calls: Number(data.llm?.calls ?? 0),
      anomalies: Number(data.llm?.anomalies ?? 0),
      anomalyRate: data.llm?.anomaly_rate ?? null,
      estimatedCalls: Number(data.llm?.estimated_calls ?? 0),
      byFinishReason: data.llm?.by_finish_reason ?? {}
    }
  };
}

export function mapProcess(process: BackendProcess): SystemProcess {
  return {
    pid: Number(process.pid ?? 0),
    name: String(process.name ?? "未知进程"),
    username: process.username,
    cpuPercent: Number(process.cpu_percent ?? 0),
    memoryBytes: Number(process.memory_bytes ?? 0),
    status: process.status
  };
}

export function mapStartupItem(item: BackendStartupItem): StartupItem {
  return {
    name: String(item.name ?? "启动项"),
    path: item.path,
    command: item.command,
    source: String(item.source ?? "unknown")
  };
}

export function mapDiagnostic(data: BackendSystemDiagnostics, startupItems?: BackendStartupItem[]): SystemDiagnostic {
  return {
    info: data.info ?? {},
    disks: (data.disks ?? []).map((disk) => ({
      device: String(disk.device ?? ""),
      mountpoint: String(disk.mountpoint ?? ""),
      fstype: disk.fstype,
      usage: disk.usage
    })),
    network: data.network ?? {},
    battery: data.battery,
    topProcesses: (data.top_processes ?? []).map(mapProcess),
    startupItems: (startupItems ?? []).map(mapStartupItem),
    suggestions: (data.suggestions ?? []).map(zhBackendText),
    product: data.product
      ? {
          name: data.product.name ? String(data.product.name) : undefined,
          version: data.product.version ? String(data.product.version) : undefined
        }
      : undefined,
    updateChannel: data.update_channel
      ? {
          configured: Boolean(data.update_channel.configured),
          status: data.update_channel.status ? String(data.update_channel.status) : undefined,
          label: data.update_channel.label ? String(data.update_channel.label) : undefined,
          detail: data.update_channel.detail ? String(data.update_channel.detail) : undefined,
          checkAction: data.update_channel.check_action ? String(data.update_channel.check_action) : undefined,
          offlineOnly: data.update_channel.offline_only === undefined ? undefined : Boolean(data.update_channel.offline_only),
          userActionLabel: data.update_channel.user_action_label ? String(data.update_channel.user_action_label) : undefined,
          nextSteps: Array.isArray(data.update_channel.next_steps) ? data.update_channel.next_steps.map(String) : undefined,
          releaseNotes: data.update_channel.release_notes
            ? {
                available: Boolean(data.update_channel.release_notes.available),
                label: data.update_channel.release_notes.label ? String(data.update_channel.release_notes.label) : undefined,
                detail: data.update_channel.release_notes.detail ? String(data.update_channel.release_notes.detail) : undefined,
                path: data.update_channel.release_notes.path ? String(data.update_channel.release_notes.path) : undefined,
                source: data.update_channel.release_notes.source ? String(data.update_channel.release_notes.source) : undefined
              }
            : undefined
        }
      : undefined,
    localPaths: data.local_paths
      ? {
          dataDir: data.local_paths.data_dir ? String(data.local_paths.data_dir) : undefined,
          database: data.local_paths.database ? String(data.local_paths.database) : undefined,
          logDirs: (data.local_paths.log_dirs ?? []).map(String)
        }
      : undefined,
    audit: data.audit
      ? {
          verification: plainRecord(data.audit.verification),
          latestEvent: plainRecord(data.audit.latest_event) ?? null
        }
      : undefined,
    lanTransport: plainRecord(data.lan_transport),
    recentCounts: numberRecord(data.recent_counts),
    recentFailureCounts: numberRecord(data.recent_failure_counts),
    diagnosticHints: (data.diagnostic_hints ?? []).map(zhBackendText),
    diagnosticScope: data.diagnostic_scope ? String(data.diagnostic_scope) : undefined,
    supportPackageRedaction: mapSupportPackageRedaction(data.support_package_redaction)
  };
}

export function mapDiagnosticExportResult(data: BackendDiagnosticExportResult): DiagnosticExportResult {
  return {
    ok: data.ok !== false,
    path: String(data.path ?? ""),
    filename: String(data.filename ?? ""),
    createdAt: String(data.created_at ?? ""),
    bytes: Number(data.bytes ?? 0),
    scope: String(data.scope ?? "local_only"),
    error: data.error ? String(data.error) : undefined
  };
}

export function mapSupportPackageRedaction(
  redaction?: BackendSupportPackageRedaction
): SystemDiagnostic["supportPackageRedaction"] {
  if (!redaction || typeof redaction !== "object") return undefined;
  const currentResponse =
    redaction.current_response && typeof redaction.current_response === "object"
      ? {
          publicSafe: redaction.current_response.public_safe === true,
          containsLocalPaths: redaction.current_response.contains_local_paths === true,
          externalReviewRequired: redaction.current_response.external_review_required !== false
        }
      : undefined;
  const externalReview =
    redaction.external_review && typeof redaction.external_review === "object" ? redaction.external_review : undefined;
  const externalReviewStatus = String(externalReview?.status ?? "manual_review_required");
  const packagePublicSafe = redaction.public_safe === true;
  const packageReviewRequired = redaction.review_before_external_sharing !== false;
  const packageExternalSharingAllowed = redaction.external_sharing_allowed === true;
  const packageFailClosed = redaction.fail_closed !== false;
  const responsePublicSafe = currentResponse?.publicSafe === true;
  const responseReviewRequired = currentResponse?.externalReviewRequired !== false;
  const responseContainsLocalPaths = currentResponse?.containsLocalPaths === true;
  const reviewPublicSafe = externalReview?.public_safe === true;
  const reviewRequired = externalReview?.required_before_external_sharing !== false;
  const reviewExternalSharingAllowed = externalReview?.external_sharing_allowed === true;
  const reviewFailClosed = externalReview?.fail_closed !== false;
  const reviewStatusAllowsSharing = externalReviewStatusAllowsSharing(externalReviewStatus);
  const publicSafeSignals = [
    packagePublicSafe,
    ...(currentResponse ? [responsePublicSafe] : []),
    ...(externalReview ? [reviewPublicSafe] : [])
  ];
  const reviewRequiredSignals = [
    packageReviewRequired,
    ...(currentResponse ? [responseReviewRequired] : []),
    ...(externalReview ? [reviewRequired] : [])
  ];
  const safetySignalsConsistent =
    Boolean(currentResponse) &&
    Boolean(externalReview) &&
    allBooleanSignalsMatch(publicSafeSignals) &&
    allBooleanSignalsMatch(reviewRequiredSignals) &&
    !(responseContainsLocalPaths && publicSafeSignals.some(Boolean)) &&
    !(!reviewStatusAllowsSharing && !reviewRequired && reviewPublicSafe);
  const blockingReasons = [
    !packagePublicSafe ? "package_public_safe_false" : "",
    packageReviewRequired ? "package_review_required" : "",
    !packageExternalSharingAllowed ? "package_external_sharing_allowed_false" : "",
    packageFailClosed ? "package_fail_closed" : "",
    !currentResponse ? "current_response_missing" : "",
    currentResponse && !responsePublicSafe ? "current_response_public_safe_false" : "",
    responseContainsLocalPaths ? "current_response_contains_local_paths" : "",
    responseReviewRequired ? "current_response_review_required" : "",
    !externalReview ? "external_review_missing" : "",
    externalReview && !reviewPublicSafe ? "external_review_public_safe_false" : "",
    reviewRequired ? "external_review_required" : "",
    externalReview && !reviewExternalSharingAllowed ? "external_review_external_sharing_allowed_false" : "",
    externalReview && reviewFailClosed ? "external_review_fail_closed" : "",
    !reviewStatusAllowsSharing ? "external_review_status_not_approved" : "",
    !safetySignalsConsistent ? "safety_signals_inconsistent_or_incomplete" : ""
  ].filter(Boolean);
  const externalSharingSafe = blockingReasons.length === 0;
  return {
    appliesTo: redaction.applies_to ? String(redaction.applies_to) : undefined,
    scope: String(redaction.scope ?? "local_only"),
    intendedAudience: String(redaction.intended_audience ?? "trusted_support"),
    publicSafe: packagePublicSafe,
    reviewBeforeExternalSharing: packageReviewRequired,
    externalSharingAllowed: packageExternalSharingAllowed,
    failClosed: packageFailClosed,
    guidance: redaction.guidance ? zhBackendText(String(redaction.guidance)) : "",
    currentResponse,
    externalReview: externalReview
      ? {
          status: externalReviewStatus,
          requiredBeforeExternalSharing: reviewRequired,
          publicSafe: externalReview.public_safe === true,
          externalSharingAllowed: reviewExternalSharingAllowed,
          failClosed: reviewFailClosed,
          checklistCount: Array.isArray(externalReview.checklist) ? externalReview.checklist.length : 0
        }
      : undefined,
    externalSharingSafe,
    safetySignalsConsistent,
    blockingReasons
  };
}

export function allBooleanSignalsMatch(values: boolean[]): boolean {
  if (values.length <= 1) return true;
  return values.every((value) => value === values[0]);
}

export function externalReviewStatusAllowsSharing(status: string): boolean {
  return [
    "approved",
    "clear",
    "cleared",
    "external_sharing_approved",
    "reviewed",
    "safe_to_share"
  ].includes(status.trim().toLowerCase());
}

export function plainRecord(value: Record<string, unknown> | null | undefined): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value : undefined;
}

export function numberRecord(value: Record<string, unknown> | undefined): Record<string, number> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, item]) => [key, Number(item)] as const)
      .filter(([, item]) => Number.isFinite(item))
  );
}
