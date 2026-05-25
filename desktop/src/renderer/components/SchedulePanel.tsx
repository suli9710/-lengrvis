import { CalendarClock, Plus, Power, PowerOff, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { MavrisApiClient } from "../lib/apiClient";
import { Badge, Panel } from "./Panel";

interface ScheduledTask {
  id: string;
  cron: string;
  goal: string;
  mode: string;
  enabled: boolean;
  next_run_at?: string;
  last_run_at?: string;
  last_status?: string;
}

interface SchedulePanelProps {
  api: MavrisApiClient;
}

export function SchedulePanel({ api }: SchedulePanelProps) {
  const [items, setItems] = useState<ScheduledTask[]>([]);
  const [draftCron, setDraftCron] = useState("*/30 * * * *");
  const [draftGoal, setDraftGoal] = useState("");
  const [draftMode, setDraftMode] = useState<"privacy" | "efficiency" | "hybrid">("privacy");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    const response = await api.listSchedules();
    if (response.ok && response.data) {
      setItems(response.data as ScheduledTask[]);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const create = async () => {
    setError(null);
    if (!draftGoal.trim()) {
      setError("请填写目标描述");
      return;
    }
    setIsSaving(true);
    const response = await api.createSchedule({
      cron: draftCron,
      goal: draftGoal.trim(),
      mode: draftMode
    });
    setIsSaving(false);
    if (!response.ok) {
      setError(response.error?.message ?? "创建失败");
      return;
    }
    setDraftGoal("");
    await refresh();
  };

  const remove = async (id: string) => {
    await api.deleteSchedule(id);
    await refresh();
  };

  const toggle = async (item: ScheduledTask) => {
    await api.enableSchedule(item.id, !item.enabled);
    await refresh();
  };

  return (
    <Panel title="定时任务" eyebrow="自动化" action={<Badge tone="info">{items.length} 项</Badge>}>
      <div className="schedule-form">
        <label className="field">
          <span>Cron 表达式</span>
          <input value={draftCron} onChange={(event) => setDraftCron(event.target.value)} placeholder="*/30 * * * *" />
        </label>
        <label className="field">
          <span>任务目标</span>
          <input value={draftGoal} onChange={(event) => setDraftGoal(event.target.value)} placeholder="每天 9 点把昨天截图归档" />
        </label>
        <label className="field">
          <span>模式</span>
          <select value={draftMode} onChange={(event) => setDraftMode(event.target.value as "privacy" | "efficiency" | "hybrid")}>
            <option value="privacy">隐私（需本地 LLM）</option>
            <option value="efficiency">效率（云端）</option>
            <option value="hybrid">混合</option>
          </select>
        </label>
        <button className="button button--primary" disabled={isSaving} onClick={() => void create()}>
          <Plus size={16} aria-hidden="true" />
          创建定时任务
        </button>
        {error ? <p className="field-error">{error}</p> : null}
      </div>

      <ul className="schedule-list">
        {items.map((item) => (
          <li key={item.id} className="schedule-row">
            <div className="schedule-meta">
              <CalendarClock size={16} aria-hidden="true" />
              <code>{item.cron}</code>
              <span className="schedule-mode">{zhMode(item.mode)}</span>
            </div>
            <p className="schedule-goal">{item.goal}</p>
            <p className="schedule-status">
              下次：{item.next_run_at ? new Date(item.next_run_at).toLocaleString() : "—"}
              {item.last_status ? `　·　上次：${item.last_status}` : ""}
            </p>
            <div className="schedule-actions">
              <button className="button button--secondary" onClick={() => void toggle(item)}>
                {item.enabled ? <PowerOff size={14} aria-hidden="true" /> : <Power size={14} aria-hidden="true" />}
                {item.enabled ? "暂停" : "启用"}
              </button>
              <button className="button button--ghost" onClick={() => void remove(item.id)}>
                <Trash2 size={14} aria-hidden="true" />
                删除
              </button>
            </div>
          </li>
        ))}
        {items.length === 0 ? <p className="schedule-empty">还没有定时任务。在上面创建一个吧。</p> : null}
      </ul>
    </Panel>
  );
}

function zhMode(mode: string): string {
  if (mode === "efficiency") return "效率";
  if (mode === "hybrid") return "混合";
  return "隐私";
}
