export interface BackendScheduledTask {
  id: string;
  cron: string;
  goal: string;
  mode: string;
  enabled: boolean;
  next_run_at?: string;
  last_run_at?: string;
  last_status?: string;
  last_task_id?: string;
  note?: string;
  created_at?: string;
  updated_at?: string;
}
