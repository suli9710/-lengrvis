export interface BackendMemory {
  id: string;
  principal_id?: string;
  workspace_id?: string;
  domain_scope?: string;
  kind: string;
  version?: number;
  supersedes?: string;
  conflict_status?: "none" | "conflicting" | "resolved" | "superseded";
  content: string;
  tags: string[];
  task_id?: string;
  source?: string;
  state?: "active" | "quarantined" | "revoked";
  user_confirmed?: boolean;
  expires_at?: string;
  reviewed_at?: string;
  reviewed_by?: string;
  use_count?: number;
  last_used_at?: string;
  created_at?: string;
}
