import type {
  ChatMessage,
  FileRevealResult,
  InstalledApp,
  InstalledSkill,
  IntentSuggestion,
  PerceptionSuggestionLaunchResponse,
  SkillImportResult,
  SkillsCatalog
} from "../../../shared/types";
import { zhBackendTaskStatus, zhBackendText } from "../zh";
import type {
  BackendChatMessage,
  BackendFileRevealResult,
  BackendInstalledApp,
  BackendInstalledSkill,
  BackendIntentSuggestion,
  BackendSkillImportResult,
  BackendSkillsCatalog,
  BackendSuggestionLaunchResponse
} from "./backendTypes";
import { optionalString } from "./mapperPrimitives";
import { mapTaskState, runEngineAgentName } from "./runMappers";

export function mapInstalledApp(app: BackendInstalledApp): InstalledApp {
  return {
    id: String(app.id ?? app.name ?? ""),
    name: String(app.name ?? app.id ?? ""),
    path: app.path,
    command: app.command,
    source: String(app.source ?? "unknown"),
    allowlisted: Boolean(app.allowlisted)
  };
}

export function mapFileRevealResult(result: BackendFileRevealResult): FileRevealResult {
  return {
    ok: result.ok !== false,
    path: optionalString(result.path),
    revealed: Boolean(result.revealed),
    shown: Boolean(result.shown ?? result.revealed),
    error: optionalString(result.error)
  };
}

export function mapSkillsCatalog(data: BackendSkillsCatalog): SkillsCatalog {
  return {
    skills: (data.skills ?? []).map(mapInstalledSkill),
    count: Number(data.count ?? data.skills?.length ?? 0),
    directories: (data.directories ?? []).map(String),
    installDirectory: String(data.install_directory ?? "")
  };
}

export function mapSkillImportResult(data: BackendSkillImportResult): SkillImportResult {
  return {
    skill: mapInstalledSkill(data.skill),
    refresh: {
      ok: Boolean(data.refresh?.ok),
      toolCount: Number(data.refresh?.tool_count ?? 0),
      skillCount: Number(data.refresh?.skill_count ?? 0)
    }
  };
}

export function mapInstalledSkill(skill: BackendInstalledSkill): InstalledSkill {
  return {
    name: String(skill.name ?? ""),
    version: String(skill.version ?? ""),
    agentOwner: String(skill.agent_owner ?? ""),
    risk: String(skill.risk ?? ""),
    root: String(skill.root ?? ""),
    manifestPath: String(skill.manifest_path ?? ""),
    status: String(skill.status ?? "error"),
    tools: (skill.tools ?? []).map((tool) => ({
      name: String(tool.name ?? ""),
      description: String(tool.description ?? ""),
      agentOwner: String(tool.agent_owner ?? ""),
      risk: String(tool.risk ?? ""),
      permissions: Array.isArray(tool.permissions) ? tool.permissions.map(String) : [],
      executionType: String(tool.execution_type ?? ""),
      entry: String(tool.entry ?? ""),
      supportsDryRun: Boolean(tool.supports_dry_run),
      requiresAuthorizedPath: Boolean(tool.requires_authorized_path),
      rollbackHint: String(tool.rollback_hint ?? "")
    })),
    safety: {
      ok: Boolean(skill.safety?.ok),
      issues: (skill.safety?.issues ?? []).map((issue) => ({
        severity: issue.severity === "warning" ? "warning" : "error",
        location: String(issue.location ?? ""),
        message: String(issue.message ?? "")
      }))
    },
    error: skill.error ? String(skill.error) : undefined
  };
}

export function mapChatMessage(message: BackendChatMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    author: message.author,
    content: zhBackendText(message.content),
    createdAt: normalizeTimestamp(message.created_at ?? message.createdAt),
    status: message.status === "failed" ? "failed" : "sent"
  };
}

export function normalizeTimestamp(value: unknown, fallback = new Date().toISOString()): string {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? fallback : new Date(timestamp).toISOString();
}

export function mapIntentSuggestion(suggestion: BackendIntentSuggestion): IntentSuggestion {
  return {
    id: suggestion.id,
    title: suggestion.title,
    prompt: zhBackendText(suggestion.prompt),
    confidence: Number(suggestion.confidence ?? 0),
    agentHint: suggestion.agent_hint,
    reason: suggestion.reason ? zhBackendText(suggestion.reason) : undefined
  };
}

export function mapSuggestionLaunchResponse(
  data: BackendSuggestionLaunchResponse,
  fallbackPrompt: string
): PerceptionSuggestionLaunchResponse {
  const runId = data.run_id ?? data.run?.run_id;
  const engine = data.engine ?? data.run?.engine;
  const phase = data.phase ?? data.run?.phase ?? "queued";
  const message = data.message ?? data.run?.message ?? fallbackPrompt;
  const createdAt = data.run?.created_at ?? new Date().toISOString();
  const updatedAt = data.run?.updated_at ?? createdAt;

  return {
    runId,
    engine,
    message: {
      id: `${runId ?? crypto.randomUUID()}-suggestion-launched`,
      role: "assistant" as const,
      author: "Lengrvis",
      content: runId ? `已根据建议启动任务：${zhBackendText(message)}` : zhBackendText(message),
      createdAt: new Date().toISOString(),
      status: "sent" as const
    },
    taskUpdates: runId
      ? [
          {
            id: runId,
            runId,
            title: zhBackendText(message),
            description: `状态：${zhBackendTaskStatus(phase)}`,
            state: mapTaskState(phase),
            agent: runEngineAgentName(engine, data.engine_capabilities ?? data.run?.engine_capabilities),
            createdAt,
            updatedAt
          }
        ]
      : []
  };
}
