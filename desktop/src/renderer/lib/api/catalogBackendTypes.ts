export interface BackendChatRequest {
  message: string;
  mode: string;
}

export interface BackendChatMessage {
  id: string;
  role: "system" | "developer" | "user" | "assistant" | "tool";
  author: string;
  content: string;
  created_at?: string;
  createdAt?: string;
  status?: string;
}

export interface BackendChatResponse {
  task_id?: string | null;
  status?: string | null;
  message: string;
  delegated?: boolean;
  agent?: string;
}

export interface BackendIntentSuggestion {
  id: string;
  title: string;
  prompt: string;
  confidence?: number;
  agent_hint?: string;
  reason?: string;
}

export interface BackendInstalledApp {
  id?: string;
  name?: string;
  path?: string;
  command?: string;
  source?: string;
  allowlisted?: boolean;
}

export interface BackendAppsResponse {
  apps: BackendInstalledApp[];
}

export interface BackendSkillTool {
  name?: string;
  description?: string;
  agent_owner?: string;
  risk?: string;
  permissions?: unknown[];
  input_schema?: unknown;
  execution_type?: string;
  entry?: string;
  supports_dry_run?: boolean;
  requires_authorized_path?: boolean;
  rollback_hint?: string;
}

export interface BackendSkillSafetyIssue {
  severity?: string;
  location?: string;
  message?: string;
}

export interface BackendInstalledSkill {
  name?: string;
  version?: string;
  agent_owner?: string;
  risk?: string;
  root?: string;
  manifest_path?: string;
  status?: string;
  tools?: BackendSkillTool[];
  safety?: {
    ok?: boolean;
    issues?: BackendSkillSafetyIssue[];
  };
  error?: string;
}

export interface BackendSkillsCatalog {
  skills?: BackendInstalledSkill[];
  count?: number;
  directories?: string[];
  install_directory?: string;
}

export interface BackendSkillImportResult {
  skill: BackendInstalledSkill;
  refresh?: BackendSkillRefresh;
}

export interface BackendSkillRefresh {
  ok?: boolean;
  tool_count?: number;
  skill_count?: number;
}
