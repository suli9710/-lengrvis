export interface BackendLlmCapabilities {
  tools?: boolean;
  structured_json?: boolean;
  vision?: boolean;
  embeddings?: boolean;
  prompt_cache?: boolean;
  responses_api?: boolean;
  reasoning_effort?: boolean;
  usage_breakdown?: boolean;
  local?: boolean;
  cloud?: boolean;
}

export interface BackendLlmProfile {
  provider_name?: string;
  model?: string;
  base_url?: string;
  wire_api?: string;
  location?: string;
  active_backend?: string;
  capabilities?: BackendLlmCapabilities;
  model_profile?: {
    model?: string;
    context_window?: number;
    max_output_tokens?: number;
    known?: boolean;
    family?: string;
  };
}

export interface BackendLlmProfileResponse {
  mode?: string;
  task?: string;
  profile?: BackendLlmProfile;
  degraded?: boolean;
  error?: string;
}

export interface BackendLlmHealth {
  active?: {
    available?: boolean;
    degraded?: boolean;
    provider?: string;
    model?: string;
    profile?: BackendLlmProfile;
    error?: string;
  };
  retry?: {
    max_retries?: number;
    backoff_seconds?: number;
    circuit_failure_threshold?: number;
    circuit_cooldown_seconds?: number;
    circuit?: {
      state?: string;
      failures?: number;
      retry_after_seconds?: number;
    };
  };
}

export interface BackendLlmCostSummary {
  window_hours?: number;
  calls?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  total_cost_usd?: number | null;
  estimated?: boolean;
  last_event_at?: string;
  by_model?: Array<{
    provider?: string;
    model?: string;
    calls?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    total_cost_usd?: number;
    estimated?: boolean;
  }>;
}

export interface BackendContextUsageWarning {
  token_count?: number;
  threshold?: number;
  percent_left?: number;
  is_above_warning_threshold?: boolean;
  is_above_error_threshold?: boolean;
  is_above_auto_compact_threshold?: boolean;
  is_at_blocking_limit?: boolean;
}

export interface BackendContextProjectionSummary {
  enabled?: boolean;
  strategy?: string;
  compacted?: boolean;
  original_tokens?: number;
  projected_tokens?: number;
  tokens_saved?: number;
  messages_removed?: number;
  adjustments?: unknown[];
  description?: string;
}

export interface BackendContextUsageProjection {
  enabled?: boolean;
  original_count?: number;
  projected_count?: number;
  original_tokens?: number;
  projected_tokens?: number;
  compacted?: boolean;
  micro_compacted?: boolean;
  history_snipped?: boolean;
  session_summary_added?: boolean;
  strategy?: string;
  source?: string;
  boundary_id?: string;
  retained_tail_message_ids?: string[];
  summary?: BackendContextProjectionSummary;
}

export interface BackendContextUsageHealth {
  status?: string;
  severity?: string;
  reason?: string;
  used_percent?: number;
  free_percent?: number;
  free_tokens?: number;
  projected_tokens?: number;
  projected_percent?: number;
  projected_free_tokens?: number;
  is_healthy?: boolean;
}

export interface BackendContextUsageLineage {
  task_id?: string;
  history_source?: string;
  message_count?: number;
  system_message_count?: number;
  agent_message_count?: number;
  message_roles?: Record<string, unknown>;
  local_tool_count?: number;
  mcp_tool_count?: number;
  session_memory_item_count?: number;
  include_registered_tools?: boolean;
  include_session_memory?: boolean;
  include_projection?: boolean;
  projection?: {
    source?: string;
    strategy?: string;
    boundary_id?: string;
    retained_tail_count?: number;
  };
}

export interface BackendContextUsage {
  total_tokens?: number;
  used_tokens?: number;
  free_tokens?: number;
  effective_context_window?: number;
  model_context_window?: number;
  auto_compact_threshold?: number;
  manual_compact_limit?: number;
  reserved_output_tokens?: number;
  warning?: BackendContextUsageWarning;
  projection?: BackendContextUsageProjection;
  health?: BackendContextUsageHealth;
  lineage?: BackendContextUsageLineage;
}
