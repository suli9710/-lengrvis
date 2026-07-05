import type { TaskEvent } from "./executionTypes";

export type ChatRole = "system" | "developer" | "user" | "assistant" | "tool";
export type ChatMessagePart =
  | {
      type: "text" | "reasoning" | "subagent" | "error" | "cancelled";
      text: string;
      title?: string;
      agent?: string;
      status?: "streaming" | "completed" | "error" | "cancelled";
    }
  | {
      type: "tool_call";
      toolName: string;
      status: "running" | "success" | "error";
      input?: string;
      output?: string;
      error?: string;
      title?: string;
    };
export type ChatMessageContent = string | ChatMessagePart[];
export type ChatMessageStatus = "sent" | "streaming" | "completed" | "failed" | "error" | "cancelled";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  author: string;
  content: ChatMessageContent;
  createdAt: string;
  status?: ChatMessageStatus;
}

export interface ChatRequest {
  content: string;
  contextTaskId?: string;
  mode?: "privacy" | "efficiency" | "hybrid";
}

export interface ChatResponse {
  message: ChatMessage;
  taskUpdates?: TaskEvent[];
  runId?: string;
  engine?: "auto" | "os" | "developer" | string;
}

export interface IntentSuggestion {
  id: string;
  title: string;
  prompt: string;
  confidence: number;
  agentHint?: string;
  reason?: string;
}

export interface PerceptionSuggestionLaunchRequest {
  suggestionId: string;
  prompt?: string;
  mode?: "privacy" | "efficiency" | "hybrid";
}

export interface PerceptionSuggestionLaunchResponse {
  message: ChatMessage;
  taskUpdates?: TaskEvent[];
  runId?: string;
  engine?: "auto" | "os" | "developer" | string;
}

export interface InstalledApp {
  id: string;
  name: string;
  path?: string;
  command?: string;
  source: "builtin" | "start_menu" | "registry" | string;
  allowlisted: boolean;
}

export interface SkillToolInfo {
  name: string;
  description: string;
  agentOwner: string;
  risk: string;
  permissions: string[];
  executionType: "python" | "shell" | "http" | string;
  entry: string;
  supportsDryRun: boolean;
  requiresAuthorizedPath: boolean;
  rollbackHint: string;
}

export interface SkillSafetyIssue {
  severity: "error" | "warning";
  location: string;
  message: string;
}

export interface InstalledSkill {
  name: string;
  version: string;
  agentOwner: string;
  risk: string;
  root: string;
  manifestPath: string;
  status: "ready" | "error" | string;
  tools: SkillToolInfo[];
  safety: {
    ok: boolean;
    issues: SkillSafetyIssue[];
  };
  error?: string;
}

export interface SkillsCatalog {
  skills: InstalledSkill[];
  count: number;
  directories: string[];
  installDirectory: string;
}

export interface SkillImportResult {
  skill: InstalledSkill;
  refresh: {
    ok: boolean;
    toolCount: number;
    skillCount: number;
  };
}
