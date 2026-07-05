import type { ContextUsage, LLMCostSummary, LLMHealthStatus, LLMProfile } from "../../../shared/llmContextTypes";
import type { BackendContextUsage, BackendLlmCostSummary, BackendLlmHealth, BackendLlmProfile } from "./llmContextBackendTypes";

export function mapLlmHealth(health: BackendLlmHealth): LLMHealthStatus {
  return {
    active: {
      available: Boolean(health.active?.available),
      degraded: Boolean(health.active?.degraded),
      provider: String(health.active?.provider ?? ""),
      model: String(health.active?.model ?? ""),
      profile: mapLlmProfile(health.active?.profile),
      error: String(health.active?.error ?? "")
    },
    retry: {
      maxRetries: Number(health.retry?.max_retries ?? 0),
      backoffSeconds: Number(health.retry?.backoff_seconds ?? 0),
      circuitFailureThreshold: Number(health.retry?.circuit_failure_threshold ?? 0),
      circuitCooldownSeconds: Number(health.retry?.circuit_cooldown_seconds ?? 0),
      circuit: {
        state: String(health.retry?.circuit?.state ?? "closed"),
        failures: Number(health.retry?.circuit?.failures ?? 0),
        retryAfterSeconds: Number(health.retry?.circuit?.retry_after_seconds ?? 0)
      }
    }
  };
}

export function mapLlmProfile(profile?: BackendLlmProfile): LLMProfile {
  const caps = profile?.capabilities ?? {};
  const modelProfile = profile?.model_profile ?? {};
  return {
    providerName: String(profile?.provider_name ?? ""),
    model: String(profile?.model ?? modelProfile.model ?? ""),
    baseUrl: String(profile?.base_url ?? ""),
    wireApi: String(profile?.wire_api ?? "chat_completions"),
    location: String(profile?.location ?? ""),
    activeBackend: String(profile?.active_backend ?? profile?.provider_name ?? ""),
    capabilities: {
      tools: Boolean(caps.tools),
      structuredJson: caps.structured_json !== false,
      vision: Boolean(caps.vision),
      embeddings: Boolean(caps.embeddings),
      promptCache: Boolean(caps.prompt_cache),
      responsesApi: Boolean(caps.responses_api),
      reasoningEffort: Boolean(caps.reasoning_effort),
      usageBreakdown: Boolean(caps.usage_breakdown),
      local: Boolean(caps.local),
      cloud: Boolean(caps.cloud)
    },
    modelProfile: {
      model: String(modelProfile.model ?? profile?.model ?? ""),
      contextWindow: Number(modelProfile.context_window ?? 0),
      maxOutputTokens: Number(modelProfile.max_output_tokens ?? 0),
      known: Boolean(modelProfile.known),
      family: String(modelProfile.family ?? "")
    }
  };
}

export function mapLlmCostSummary(summary: BackendLlmCostSummary): LLMCostSummary {
  return {
    windowHours: Number(summary.window_hours ?? 24),
    calls: Number(summary.calls ?? 0),
    promptTokens: Number(summary.prompt_tokens ?? 0),
    completionTokens: Number(summary.completion_tokens ?? 0),
    totalTokens: Number(summary.total_tokens ?? 0),
    totalCostUsd: typeof summary.total_cost_usd === "number" ? summary.total_cost_usd : null,
    estimated: Boolean(summary.estimated),
    lastEventAt: String(summary.last_event_at ?? ""),
    byModel: (summary.by_model ?? []).map((item) => ({
      provider: String(item.provider ?? ""),
      model: String(item.model ?? ""),
      calls: Number(item.calls ?? 0),
      promptTokens: Number(item.prompt_tokens ?? 0),
      completionTokens: Number(item.completion_tokens ?? 0),
      totalTokens: Number(item.total_tokens ?? 0),
      totalCostUsd: Number(item.total_cost_usd ?? 0),
      estimated: Boolean(item.estimated)
    }))
  };
}

export function mapContextUsage(usage: BackendContextUsage): ContextUsage {
  const warning = usage.warning ?? {};
  const projection = usage.projection ?? {};
  const projectionSummary = projection.summary ?? {};
  const effectiveContextWindow = Number(usage.effective_context_window ?? usage.model_context_window ?? 0);
  const usedTokens = Number(usage.used_tokens ?? warning.token_count ?? 0);
  const projectedTokens = Number(projectionSummary.projected_tokens ?? projection.projected_tokens ?? usedTokens);
  const freeTokens = Number(usage.free_tokens ?? Math.max(0, effectiveContextWindow - usedTokens));
  const usedPercent = effectiveContextWindow > 0 ? Math.round((usedTokens / effectiveContextWindow) * 10000) / 100 : 0;
  const projectedPercent =
    effectiveContextWindow > 0 ? Math.round((projectedTokens / effectiveContextWindow) * 10000) / 100 : usedPercent;
  const fallbackSeverity = warning.is_at_blocking_limit || warning.is_above_error_threshold
    ? "error"
    : warning.is_above_warning_threshold
      ? "warning"
      : "ok";
  const fallbackStatus = fallbackSeverity === "error" ? "critical" : fallbackSeverity === "warning" ? "watch" : "healthy";
  const health = usage.health ?? {};
  const lineage = usage.lineage ?? {};
  const lineageProjection = lineage.projection ?? {};

  return {
    totalTokens: Number(usage.total_tokens ?? usedTokens + freeTokens),
    usedTokens,
    freeTokens,
    effectiveContextWindow,
    modelContextWindow: Number(usage.model_context_window ?? effectiveContextWindow),
    autoCompactThreshold: Number(usage.auto_compact_threshold ?? warning.threshold ?? 0),
    manualCompactLimit: Number(usage.manual_compact_limit ?? 0),
    reservedOutputTokens: Number(usage.reserved_output_tokens ?? 0),
    warning: {
      tokenCount: Number(warning.token_count ?? usedTokens),
      threshold: Number(warning.threshold ?? 0),
      percentLeft: Number(warning.percent_left ?? Math.max(0, 100 - usedPercent)),
      isAboveWarningThreshold: Boolean(warning.is_above_warning_threshold),
      isAboveErrorThreshold: Boolean(warning.is_above_error_threshold),
      isAboveAutoCompactThreshold: Boolean(warning.is_above_auto_compact_threshold),
      isAtBlockingLimit: Boolean(warning.is_at_blocking_limit)
    },
    health: {
      status: contextHealthStatus(health.status, fallbackStatus),
      severity: contextHealthSeverity(health.severity, fallbackSeverity),
      reason: String(health.reason ?? contextHealthFallbackReason(fallbackSeverity)),
      usedPercent: Number(health.used_percent ?? usedPercent),
      freePercent: Number(health.free_percent ?? Math.max(0, 100 - usedPercent)),
      freeTokens: Number(health.free_tokens ?? freeTokens),
      projectedTokens: Number(health.projected_tokens ?? projectedTokens),
      projectedPercent: Number(health.projected_percent ?? projectedPercent),
      projectedFreeTokens: Number(health.projected_free_tokens ?? Math.max(0, effectiveContextWindow - projectedTokens)),
      isHealthy: health.is_healthy === undefined ? fallbackSeverity === "ok" : Boolean(health.is_healthy)
    },
    projection: {
      enabled: Boolean(projectionSummary.enabled ?? projection.enabled),
      strategy: String(projectionSummary.strategy ?? projection.strategy ?? "none"),
      compacted: Boolean(projectionSummary.compacted ?? projection.compacted),
      originalTokens: Number(projectionSummary.original_tokens ?? projection.original_tokens ?? usedTokens),
      projectedTokens,
      tokensSaved: Number(
        projectionSummary.tokens_saved ??
          Math.max(0, Number(projection.original_tokens ?? usedTokens) - Number(projection.projected_tokens ?? usedTokens))
      ),
      messagesRemoved: Number(
        projectionSummary.messages_removed ??
          Math.max(0, Number(projection.original_count ?? 0) - Number(projection.projected_count ?? 0))
      ),
      adjustments: Array.isArray(projectionSummary.adjustments)
        ? projectionSummary.adjustments.map((item) => String(item))
        : [],
      description: String(projectionSummary.description ?? "Projection summary is unavailable.")
    },
    lineage: {
      taskId: String(lineage.task_id ?? ""),
      historySource: String(lineage.history_source ?? "unknown"),
      messageCount: Number(lineage.message_count ?? 0),
      systemMessageCount: Number(lineage.system_message_count ?? 0),
      agentMessageCount: Number(lineage.agent_message_count ?? 0),
      messageRoles: objectRecord(lineage.message_roles),
      localToolCount: Number(lineage.local_tool_count ?? 0),
      mcpToolCount: Number(lineage.mcp_tool_count ?? 0),
      sessionMemoryItemCount: Number(lineage.session_memory_item_count ?? 0),
      includeRegisteredTools: lineage.include_registered_tools !== false,
      includeSessionMemory: lineage.include_session_memory !== false,
      includeProjection: lineage.include_projection !== false,
      projection: {
        source: String(lineageProjection.source ?? "context_usage"),
        strategy: String(lineageProjection.strategy ?? projection.strategy ?? "none"),
        boundaryId: String(lineageProjection.boundary_id ?? projection.boundary_id ?? ""),
        retainedTailCount: Number(
          lineageProjection.retained_tail_count ??
            (Array.isArray(projection.retained_tail_message_ids) ? projection.retained_tail_message_ids.length : 0)
        )
      }
    }
  };
}

export function contextHealthStatus(value: unknown, fallback: ContextUsage["health"]["status"]): ContextUsage["health"]["status"] {
  if (value === "healthy" || value === "managed" || value === "watch" || value === "critical" || value === "blocked") {
    return value;
  }
  return fallback;
}

export function contextHealthSeverity(
  value: unknown,
  fallback: ContextUsage["health"]["severity"]
): ContextUsage["health"]["severity"] {
  if (value === "ok" || value === "warning" || value === "error") return value;
  return fallback;
}

export function contextHealthFallbackReason(severity: ContextUsage["health"]["severity"]): string {
  if (severity === "error") return "Context is close to its limit.";
  if (severity === "warning") return "Context is getting busy.";
  return "Context has room for the next step.";
}

export function objectRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, Number(item ?? 0)])
  );
}
