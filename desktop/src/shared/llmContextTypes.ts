export interface LLMCapabilities {
  tools: boolean;
  structuredJson: boolean;
  vision: boolean;
  embeddings: boolean;
  promptCache: boolean;
  responsesApi: boolean;
  reasoningEffort: boolean;
  usageBreakdown: boolean;
  local: boolean;
  cloud: boolean;
}

export interface LLMProfile {
  providerName: string;
  model: string;
  baseUrl: string;
  wireApi: string;
  location: "local" | "cloud" | string;
  activeBackend: string;
  capabilities: LLMCapabilities;
  modelProfile: {
    model: string;
    contextWindow: number;
    maxOutputTokens: number;
    known: boolean;
    family: string;
  };
}

export interface LLMRetryStatus {
  maxRetries: number;
  backoffSeconds: number;
  circuitFailureThreshold: number;
  circuitCooldownSeconds: number;
  circuit: {
    state: "open" | "closed" | string;
    failures: number;
    retryAfterSeconds: number;
  };
}

export interface LLMHealthStatus {
  active: {
    available: boolean;
    degraded: boolean;
    provider: string;
    model: string;
    profile: LLMProfile;
    error: string;
  };
  retry: LLMRetryStatus;
}

export interface LLMCostSummary {
  windowHours: number;
  calls: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  totalCostUsd: number | null;
  estimated: boolean;
  lastEventAt: string;
  byModel: Array<{
    provider: string;
    model: string;
    calls: number;
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    totalCostUsd: number;
    estimated: boolean;
  }>;
}

export interface ContextUsageHealth {
  status: "healthy" | "managed" | "watch" | "critical" | "blocked" | "unknown";
  severity: "ok" | "warning" | "error" | "unknown";
  reason: string;
  usedPercent: number;
  freePercent: number;
  freeTokens: number;
  projectedTokens: number;
  projectedPercent: number;
  projectedFreeTokens: number;
  isHealthy: boolean;
}

export interface ContextProjectionSummary {
  enabled: boolean;
  strategy: string;
  compacted: boolean;
  originalTokens: number;
  projectedTokens: number;
  tokensSaved: number;
  messagesRemoved: number;
  adjustments: string[];
  description: string;
}

export interface ContextUsageLineage {
  taskId: string;
  historySource: string;
  messageCount: number;
  systemMessageCount: number;
  agentMessageCount: number;
  messageRoles: Record<string, number>;
  localToolCount: number;
  mcpToolCount: number;
  sessionMemoryItemCount: number;
  includeRegisteredTools: boolean;
  includeSessionMemory: boolean;
  includeProjection: boolean;
  projection: {
    source: string;
    strategy: string;
    boundaryId: string;
    retainedTailCount: number;
  };
}

export interface ContextUsage {
  totalTokens: number;
  usedTokens: number;
  freeTokens: number;
  effectiveContextWindow: number;
  modelContextWindow: number;
  autoCompactThreshold: number;
  manualCompactLimit: number;
  reservedOutputTokens: number;
  warning: {
    tokenCount: number;
    threshold: number;
    percentLeft: number;
    isAboveWarningThreshold: boolean;
    isAboveErrorThreshold: boolean;
    isAboveAutoCompactThreshold: boolean;
    isAtBlockingLimit: boolean;
  };
  health: ContextUsageHealth;
  projection: ContextProjectionSummary;
  lineage: ContextUsageLineage;
}
