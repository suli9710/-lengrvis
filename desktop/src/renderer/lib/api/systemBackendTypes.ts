export interface BackendLocalMetrics {
  window_days?: number;
  generated_at?: string;
  tasks?: {
    total?: number;
    terminal?: number;
    succeeded?: number;
    success_rate?: number | null;
    by_status?: Record<string, number>;
  };
  runs?: {
    total?: number;
    by_phase?: Record<string, number>;
  };
  recovery?: {
    reflections_started?: number;
    runs_with_reflection?: number;
    recovery_trigger_rate?: number | null;
    decided_actions?: Record<string, number>;
    ask_user_share?: number | null;
  };
  llm?: {
    calls?: number;
    anomalies?: number;
    anomaly_rate?: number | null;
    estimated_calls?: number;
    by_finish_reason?: Record<string, number>;
  };
}

export interface BackendSystemInfo {
  platform?: string;
  system?: string;
  machine?: string;
}

export interface BackendProcess {
  pid?: number;
  name?: string;
  username?: string;
  cpu_percent?: number;
  memory_bytes?: number;
  status?: string;
}

export interface BackendProcessesResponse {
  processes: BackendProcess[];
  count?: number;
}

export interface BackendStartupItem {
  name?: string;
  path?: string;
  command?: string;
  source?: string;
}

export interface BackendStartupResponse {
  startup_items: BackendStartupItem[];
  count?: number;
}

export interface BackendDisk {
  device?: string;
  mountpoint?: string;
  fstype?: string;
  usage?: {
    total?: number;
    used?: number;
    free?: number;
    percent?: number;
  };
}

export interface BackendSystemDiagnostics {
  info?: Record<string, unknown>;
  disks?: BackendDisk[];
  network?: Record<string, unknown>;
  battery?: Record<string, unknown> | null;
  top_processes?: BackendProcess[];
  suggestions?: string[];
  product?: {
    name?: string;
    version?: string;
  };
  update_channel?: {
    configured?: boolean;
    status?: string;
    label?: string;
    detail?: string;
    check_action?: string;
    offline_only?: boolean;
    user_action_label?: string;
    next_steps?: unknown[];
    release_notes?: {
      available?: boolean;
      label?: string;
      detail?: string;
      path?: string;
      source?: string;
    };
  };
  local_paths?: {
    data_dir?: string;
    database?: string;
    log_dirs?: string[];
  };
  audit?: {
    verification?: Record<string, unknown>;
    latest_event?: Record<string, unknown> | null;
  };
  lan_transport?: Record<string, unknown>;
  recent_counts?: Record<string, unknown>;
  recent_failure_counts?: Record<string, unknown>;
  diagnostic_hints?: string[];
  diagnostic_scope?: string;
  support_package_redaction?: BackendSupportPackageRedaction;
}

export interface BackendSupportPackageRedaction {
  applies_to?: string;
  scope?: string;
  intended_audience?: string;
  public_safe?: boolean;
  review_before_external_sharing?: boolean;
  external_sharing_allowed?: boolean;
  fail_closed?: boolean;
  current_response?: {
    public_safe?: boolean;
    contains_local_paths?: boolean;
    external_review_required?: boolean;
  };
  guidance?: string;
  external_review?: {
    status?: string;
    required_before_external_sharing?: boolean;
    public_safe?: boolean;
    external_sharing_allowed?: boolean;
    fail_closed?: boolean;
    checklist?: unknown[];
  };
}

export interface BackendDiagnosticExportResult {
  ok?: boolean;
  path?: string;
  filename?: string;
  created_at?: string;
  bytes?: number;
  scope?: string;
  error?: string;
}
