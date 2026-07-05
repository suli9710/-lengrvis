import type { InstalledApp } from "./catalogTypes";

export interface LocalMetricsSummary {
  windowDays: number;
  generatedAt: string;
  tasks: {
    total: number;
    terminal: number;
    succeeded: number;
    successRate: number | null;
    byStatus: Record<string, number>;
  };
  runs: {
    total: number;
    byPhase: Record<string, number>;
  };
  recovery: {
    reflectionsStarted: number;
    runsWithReflection: number;
    recoveryTriggerRate: number | null;
    decidedActions: Record<string, number>;
    askUserShare: number | null;
  };
  llm: {
    calls: number;
    anomalies: number;
    anomalyRate: number | null;
    estimatedCalls: number;
    byFinishReason: Record<string, number>;
  };
}

export interface SystemProcess {
  pid: number;
  name: string;
  username?: string;
  cpuPercent: number;
  memoryBytes: number;
  status?: string;
}

export interface StartupItem {
  name: string;
  path?: string;
  command?: string;
  source: string;
}

export interface DiskUsage {
  total?: number;
  used?: number;
  free?: number;
  percent?: number;
}

export interface DiskInfo {
  device: string;
  mountpoint: string;
  fstype?: string;
  usage?: DiskUsage;
}

export interface SystemDiagnosticProduct {
  name?: string;
  version?: string;
}

export interface SystemDiagnosticReleaseNotes {
  available: boolean;
  label?: string;
  detail?: string;
  path?: string;
  source?: "local_file" | "package_notes" | "not_packaged" | string;
}

export interface SystemDiagnosticUpdateChannel {
  configured: boolean;
  status?: "not_configured" | string;
  label?: string;
  detail?: string;
  checkAction?: "refresh_local_status" | string;
  offlineOnly?: boolean;
  userActionLabel?: string;
  nextSteps?: string[];
  releaseNotes?: SystemDiagnosticReleaseNotes;
}

export interface SystemDiagnosticLocalPaths {
  dataDir?: string;
  database?: string;
  logDirs: string[];
}

export interface SystemDiagnosticAudit {
  verification?: Record<string, unknown>;
  latestEvent?: Record<string, unknown> | null;
}

export interface SystemDiagnosticExternalReview {
  status: string;
  requiredBeforeExternalSharing: boolean;
  publicSafe: boolean;
  externalSharingAllowed: boolean;
  failClosed: boolean;
  checklistCount: number;
}

export interface SystemDiagnosticCurrentResponseReview {
  publicSafe: boolean;
  containsLocalPaths: boolean;
  externalReviewRequired: boolean;
}

export interface SystemDiagnosticSupportPackageRedaction {
  appliesTo?: string;
  scope: string;
  intendedAudience: string;
  publicSafe: boolean;
  reviewBeforeExternalSharing: boolean;
  externalSharingAllowed: boolean;
  failClosed: boolean;
  guidance: string;
  currentResponse?: SystemDiagnosticCurrentResponseReview;
  externalReview?: SystemDiagnosticExternalReview;
  externalSharingSafe: boolean;
  safetySignalsConsistent: boolean;
  blockingReasons: string[];
}

export interface SystemDiagnostic {
  info: Record<string, unknown>;
  disks: DiskInfo[];
  network: Record<string, unknown>;
  battery?: Record<string, unknown> | null;
  topProcesses: SystemProcess[];
  startupItems?: StartupItem[];
  suggestions: string[];
  product?: SystemDiagnosticProduct;
  updateChannel?: SystemDiagnosticUpdateChannel;
  localPaths?: SystemDiagnosticLocalPaths;
  audit?: SystemDiagnosticAudit;
  lanTransport?: Record<string, unknown>;
  recentCounts?: Record<string, number>;
  recentFailureCounts?: Record<string, number>;
  diagnosticHints?: string[];
  diagnosticScope?: string;
  supportPackageRedaction?: SystemDiagnosticSupportPackageRedaction;
}

export interface DiagnosticExportResult {
  ok: boolean;
  path: string;
  filename: string;
  createdAt: string;
  bytes: number;
  scope: string;
  error?: string;
}

export interface SystemInfo {
  appVersion: string;
  electronVersion: string;
  chromeVersion: string;
  nodeVersion: string;
  platform: string;
  arch: string;
  backendBaseUrl: string;
  diagnostics?: SystemDiagnostic;
  processes?: SystemProcess[];
  startupItems?: StartupItem[];
  installedApps?: InstalledApp[];
}
