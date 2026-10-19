import type { ApiResponse, DesktopRunMode } from "./desktopBridgeTypes";
import type { ChatMessage } from "./catalogTypes";

export type ButlerMatterStatus = "queued" | "planning" | "running" | "waiting_input" | "waiting_approval" | "waiting_event" | "paused" | "completed" | "failed" | "cancelled";
export type AutonomyAction = "observe" | "file_modify" | "ui_edit" | "browser_prepare";
export type ButlerControlAction = "enable" | "disable" | "pause" | "resume" | "coordination_enable" | "coordination_disable" | "auto_memory_enable" | "auto_memory_disable";
export type ButlerMatterControlAction = "pause" | "resume" | "cancel";

export interface ButlerInteraction {
  id: string;
  kind: "information" | "choose_matter" | "choose_dependency" | "approval" | "takeover" | "reconcile";
  message: string;
  fields: string[];
  candidates: string[];
  approval_id: string;
}

/** Public projection only. Execution tickets and internal grants never enter the renderer. */
export interface ButlerMatter {
  id: string;
  version: number;
  goal: string;
  source: "user" | "event" | "schedule";
  status: ButlerMatterStatus;
  mode: string;
  priority: number;
  priority_explicit?: boolean;
  depends_on?: string[];
  coordination?: {
    blocked: boolean;
    blocking_dependencies: Array<{ matter_id: string; goal: string; reason: string; needs_decision: boolean }>;
    needs_decision: boolean;
    arrangement_reason: string;
    rank: number;
    can_arrange_dependencies: boolean;
    suggestions: string[];
  };
  constraints?: { coordination_request?: { parents: string[]; children: string[]; parent_goal: string; child_goal: string } };
  due_at: string;
  progress: string;
  interaction: ButlerInteraction | null;
  waiting: { kind: string; subject: string; due_at: string; baseline?: string } | null;
  task_ids: string[];
  run_ids: string[];
  active_task_id: string;
  active_run_id: string;
  completion_evidence: Array<Record<string, unknown>>;
  artifacts?: Array<{ id: string; kind: "xlsx" | "docx"; name: string; verified: boolean; opened: boolean; status: string }>;
  memory_ids?: string[];
  undo_available?: boolean;
  created_at: string;
  updated_at: string;
}

export interface AutonomyScopeInput {
  id?: string;
  version?: number;
  label: string;
  directories: string[];
  applications: string[];
  origins: string[];
  allowed_actions: AutonomyAction[];
  account_id?: string;
}

export interface AutonomyScope extends AutonomyScopeInput {
  id: string;
  version: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ButlerOverview {
  paused: boolean;
  enabled: boolean;
  coordination_enabled?: boolean;
  auto_memory_enabled?: boolean;
  matters: ButlerMatter[];
  scopes: AutonomyScope[];
  defaults: { event_merge_seconds: number; background_concurrency: number; desktop_concurrency: number };
}

export interface ButlerMessageInput {
  message: string;
  mode: DesktopRunMode;
  matter_id?: string;
}

export interface ButlerResponseInput {
  version: number;
  interaction_id: string;
  message?: string;
  answers?: Record<string, unknown>;
}

export interface ButlerMessageResult {
  matter?: ButlerMatter;
  reply: string;
  user_message?: ChatMessage;
  assistant_message?: ChatMessage;
}

export interface ButlerArrangementInput {
  matter_id: string;
  version: number;
  depends_on?: string[];
  priority?: number;
  due_at?: string;
}

export interface ButlerDesktopBridge {
  openArtifact: (request: { matter_id: string; artifact_id: string; version: number }) => Promise<ApiResponse<{ ok: boolean; artifact_id: string; opened: boolean }>>;
  overview: () => Promise<ApiResponse<ButlerOverview>>;
  message: (request: ButlerMessageInput) => Promise<ApiResponse<ButlerMessageResult>>;
  respond: (request: ButlerResponseInput & { matter_id: string }) => Promise<ApiResponse<ButlerMessageResult>>;
  matterControl: (request: { matter_id: string; action: ButlerMatterControlAction }) => Promise<ApiResponse<ButlerMatter>>;
  arrangement: (request: ButlerArrangementInput) => Promise<ApiResponse<ButlerMatter>>;
  control: (request: { action: ButlerControlAction }) => Promise<ApiResponse<Pick<ButlerOverview, "paused" | "enabled" | "coordination_enabled" | "auto_memory_enabled">>>;
  createScope: (request: AutonomyScopeInput) => Promise<ApiResponse<AutonomyScope>>;
  revokeScope: (request: { scope_id: string; version: number }) => Promise<ApiResponse<AutonomyScope>>;
  undo: (request: { matter_id: string; version: number }) => Promise<ApiResponse<{ matter: ButlerMatter; outcomes: Array<Record<string, unknown>> }>>;
}
