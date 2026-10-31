import { CalendarClock, Plus, Power, PowerOff, Trash2 } from "lucide-react";
import { useCallback, useState } from "react";

import { LengrvisApiClient } from "../lib/apiClient";
import { Badge, Panel } from "./Panel";
import { CollectionPanelStatus, useCollectionPanelState } from "./useCollectionPanelState";

interface ScheduledTask {
  id: string;
  cron: string;
  timezone?: string;
  goal: string;
  mode: string;
  enabled: boolean;
  next_run_at?: string;
  last_run_at?: string;
  last_status?: string;
}

interface SchedulePanelProps {
  api: LengrvisApiClient;
}

export function SchedulePanel({ api }: SchedulePanelProps) {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const [draftCron, setDraftCron] = useState("*/30 * * * *");
  const [draftGoal, setDraftGoal] = useState("");
  const [draftMode, setDraftMode] = useState<"privacy" | "efficiency" | "hybrid">("privacy");
  const loader = useCallback(() => api.listSchedules(), [api]);
  const {
    items,
    isLoading,
    loadError,
    mutationError,
    pendingAction,
    mutate,
    refresh,
    setMutationError
  } = useCollectionPanelState<ScheduledTask>(loader, "无法读取定时任务");

  const createSchedule = async () => {
    setMutationError(null);
    const goal = draftGoal.trim();
    if (!goal) {
      setMutationError("请填写目标描述");
      return;
    }
    await mutate("create", () => api.createSchedule({ cron: draftCron, timezone, goal, mode: draftMode }), "创建定时任务失败", async () => {
      setDraftGoal("");
      await refresh();
    });
  };

  const remove = async (item: ScheduledTask) => {
    await mutate(`delete:${item.id}`, () => api.deleteSchedule(item.id), "删除定时任务失败", refresh);
  };

  const toggle = async (item: ScheduledTask) => {
    const failure = `${item.enabled ? "暂停" : "启用"}定时任务失败`;
    await mutate(`toggle:${item.id}`, () => api.enableSchedule(item.id, !item.enabled), failure, refresh);
  };

  const isMutating = pendingAction !== null;

  return (
    <Panel title="定时任务" eyebrow="自动化" action={<Badge tone={loadError ? "danger" : "info"}>{items.length} 项</Badge>}>
      <div className="schedule-panel" aria-busy={isLoading || isMutating}>
        <div className="schedule-form">
          <label className="field">
            <span>运行周期</span>
            <input
              aria-label="运行周期"
              value={draftCron}
              disabled={pendingAction === "create"}
              onChange={(event) => setDraftCron(event.currentTarget.value)}
              placeholder="*/30 * * * *"
            />
            <em className="field-hint">按 {timezone} 时区运行。Cron 表达式 */30 * * * * 表示每 30 分钟运行一次。</em>
          </label>
          <label className="field schedule-form__goal">
            <span>任务目标</span>
            <input
              aria-label="任务目标"
              value={draftGoal}
              disabled={pendingAction === "create"}
              onChange={(event) => setDraftGoal(event.currentTarget.value)}
              placeholder="每天 9 点把昨天截图归档"
            />
          </label>
          <label className="field">
            <span>模式</span>
            <select
              aria-label="任务模式"
              value={draftMode}
              disabled={pendingAction === "create"}
              onChange={(event) => setDraftMode(event.currentTarget.value as "privacy" | "efficiency" | "hybrid")}
            >
              <option value="privacy">隐私（需本地 LLM）</option>
              <option value="efficiency">效率（云端）</option>
              <option value="hybrid">混合</option>
            </select>
          </label>
          <button
            className="button button--primary schedule-form__submit"
            type="button"
            aria-label={pendingAction === "create" ? "正在创建定时任务" : "创建定时任务"}
            aria-busy={pendingAction === "create"}
            disabled={isMutating}
            onClick={() => void createSchedule()}
          >
            <Plus size={16} aria-hidden="true" />
            <span>{pendingAction === "create" ? "正在创建" : "创建定时任务"}</span>
          </button>
          {mutationError ? <p className="field-error panel-inline-error" role="alert">{mutationError}</p> : null}
        </div>

        <CollectionPanelStatus
          isLoading={isLoading}
          loadError={loadError}
          loadingLabel="正在读取定时任务…"
          onRetry={() => void refresh()}
        />

        {items.length ? (
          <ul className="schedule-list" aria-label="定时任务列表">
            {items.map((item) => {
              const isToggling = pendingAction === `toggle:${item.id}`;
              const isDeleting = pendingAction === `delete:${item.id}`;
              const toggleAction = item.enabled ? "暂停" : "启用";
              return (
                <li
                  key={item.id}
                  className={`schedule-row${item.enabled ? "" : " schedule-row--disabled"}`}
                  aria-busy={isToggling || isDeleting}
                >
                  <div className="schedule-meta muted">
                    <CalendarClock size={16} aria-hidden="true" />
                    <code>{item.cron}</code>
                    <span>{item.timezone || "UTC"}</span>
                    <span className="schedule-mode">{zhMode(item.mode)}</span>
                    <span className={`schedule-enabled schedule-enabled--${item.enabled ? "on" : "off"}`}>
                      {item.enabled ? "运行中" : "已暂停"}
                    </span>
                  </div>
                  <p className="schedule-goal">{item.goal}</p>
                  <p className="schedule-status muted">
                    下次：{item.next_run_at ? new Date(item.next_run_at).toLocaleString(undefined, { timeZone: item.timezone || "UTC" }) : "—"}
                    {item.last_status ? ` · 上次：${zhStatus(item.last_status)}` : ""}
                  </p>
                  <div className="schedule-actions">
                    <button
                      className="button button--secondary"
                      type="button"
                      aria-label={`${isToggling ? "正在" : ""}${toggleAction}定时任务：${item.goal}`}
                      aria-busy={isToggling}
                      title={`${toggleAction}定时任务`}
                      disabled={isMutating}
                      onClick={() => void toggle(item)}
                    >
                      {item.enabled ? (
                        <PowerOff size={14} aria-hidden="true" />
                      ) : (
                        <Power size={14} aria-hidden="true" />
                      )}
                      <span>{isToggling ? (item.enabled ? "正在暂停" : "正在启用") : item.enabled ? "暂停" : "启用"}</span>
                    </button>
                    <button
                      className="button button--ghost"
                      type="button"
                      aria-label={`${isDeleting ? "正在删除" : "删除"}定时任务：${item.goal}`}
                      aria-busy={isDeleting}
                      disabled={isMutating}
                      onClick={() => void remove(item)}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                      <span>{isDeleting ? "正在删除" : "删除"}</span>
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}

        {!isLoading && !loadError && items.length === 0 ? (
          <p className="empty-state schedule-empty">还没有定时任务。在上面创建一个吧。</p>
        ) : null}
      </div>
    </Panel>
  );
}

function zhMode(mode: string): string {
  if (mode === "efficiency") return "效率";
  if (mode === "hybrid") return "混合";
  return "隐私";
}

function zhStatus(status: string): string {
  const labels: Record<string, string> = {
    completed: "已完成", failed: "失败", denied: "已拒绝", cancelled: "已取消",
    rolled_back: "已回滚", repair_required: "需要修复", awaiting_approval: "等待审批",
    paused: "已暂停", running: "执行中"
  };
  return labels[status] || status.replace(/^failed:/, "失败：");
}
