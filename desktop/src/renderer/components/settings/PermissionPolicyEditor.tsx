import { ShieldCheck, Trash2 } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

export type PermissionEffect = "allow" | "deny";

interface PermissionTimeWindow {
  days: number[];
  start: string;
  end: string;
  timezone?: string;
}

interface PermissionRule {
  id: string;
  name: string;
  effect: PermissionEffect;
  tools: string[];
  pathPatterns: string[];
  timeWindows: PermissionTimeWindow[];
  reason: string;
  enabled: boolean;
}

export interface PermissionPolicy {
  rules: PermissionRule[];
  updatedAt?: string;
}

export interface BackendPermissionPolicy {
  rules?: BackendPermissionRule[];
  updated_at?: string;
}

export interface BackendPermissionRule {
  id?: string;
  name?: string;
  effect?: PermissionEffect;
  tool?: string;
  tools?: string[];
  path_pattern?: string;
  path_patterns?: string[];
  time_window?: BackendPermissionTimeWindow | null;
  time_windows?: BackendPermissionTimeWindow[];
  enabled?: boolean;
  reason?: string;
}

interface BackendPermissionTimeWindow {
  days?: number[];
  start?: string;
  end?: string;
  timezone?: string;
}

export const DEFAULT_PERMISSION_POLICY: PermissionPolicy = { rules: [] };

export const DEFAULT_PERMISSION_RULE_DRAFT = {
  effect: "deny" as PermissionEffect,
  tool: "file.trash",
  pathPattern: "*",
  days: "weekend",
  start: "00:00",
  end: "23:59",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
  reason: "周末禁止删除文件。"
};

export type PermissionRuleDraft = typeof DEFAULT_PERMISSION_RULE_DRAFT;

interface PermissionPolicyEditorProps {
  policy: PermissionPolicy;
  draft: PermissionRuleDraft;
  status: string;
  isSaving: boolean;
  onDraftChange: Dispatch<SetStateAction<PermissionRuleDraft>>;
  onSave: () => void;
  onDelete: (ruleId: string) => void;
}

export function PermissionPolicyEditor({
  policy,
  draft,
  status,
  isSaving,
  onDraftChange,
  onSave,
  onDelete
}: PermissionPolicyEditorProps) {
  return (
    <fieldset className="mcp-servers">
      <legend>权限策略</legend>
      <div className="settings-grid settings-grid--balanced">
        <label className="field">
          <span>规则效果</span>
          <select
            value={draft.effect}
            onChange={(event) =>
              onDraftChange((current) => ({ ...current, effect: event.target.value as PermissionEffect }))
            }
          >
            <option value="deny">拒绝</option>
            <option value="allow">允许</option>
          </select>
        </label>
        <label className="field">
          <span>工具</span>
          <input
            value={draft.tool}
            onChange={(event) => onDraftChange((current) => ({ ...current, tool: event.target.value }))}
            placeholder="file.trash"
          />
        </label>
        <label className="field">
          <span>路径模式</span>
          <input
            value={draft.pathPattern}
            onChange={(event) => onDraftChange((current) => ({ ...current, pathPattern: event.target.value }))}
            placeholder="*"
          />
        </label>
        <label className="field">
          <span>日期</span>
          <input
            value={draft.days}
            onChange={(event) => onDraftChange((current) => ({ ...current, days: event.target.value }))}
            placeholder="weekend 或 0,1,2"
          />
        </label>
        <label className="field">
          <span>开始时间</span>
          <input
            type="time"
            value={draft.start}
            onChange={(event) => onDraftChange((current) => ({ ...current, start: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>结束时间</span>
          <input
            type="time"
            value={draft.end}
            onChange={(event) => onDraftChange((current) => ({ ...current, end: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>时区</span>
          <input
            value={draft.timezone}
            onChange={(event) => onDraftChange((current) => ({ ...current, timezone: event.target.value }))}
            placeholder="Asia/Shanghai"
          />
        </label>
        <label className="field">
          <span>原因</span>
          <input
            value={draft.reason}
            onChange={(event) => onDraftChange((current) => ({ ...current, reason: event.target.value }))}
          />
        </label>
      </div>
      <div className="button-row">
        <button className="button button--primary" onClick={onSave} disabled={isSaving} type="button">
          <ShieldCheck size={16} aria-hidden="true" />
          {isSaving ? "保存中" : "保存规则"}
        </button>
        {status ? <span className="muted">{status}</span> : null}
      </div>
      {policy.rules.length === 0 ? (
        <p className="muted">尚未配置权限规则。</p>
      ) : (
        <ul className="mcp-servers__list">
          {policy.rules.map((rule) => (
            <li className="mcp-servers__row" key={rule.id}>
              <span>
                {rule.enabled ? "" : "[已禁用] "}
                {rule.effect === "allow" ? "允许" : "拒绝"} {rule.tools.join(", ") || "*"} 作用于 {rule.pathPatterns.join(", ") || "*"}
                {rule.timeWindows.length ? `，时间：${rule.timeWindows.map(formatTimeWindow).join("; ")}` : ""}
              </span>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => onDelete(rule.id)}
                aria-label={`删除权限规则 ${rule.id}`}
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

export function buildPermissionRule(draft: PermissionRuleDraft): BackendPermissionRule {
  return {
    id: `perm_${crypto.randomUUID().replace(/-/g, "")}`,
    name: `${draft.effect} ${draft.tool}`,
    effect: draft.effect,
    tools: [draft.tool.trim() || "*"],
    path_patterns: [draft.pathPattern.trim() || "*"],
    time_windows: [{
      days: parsePermissionDays(draft.days),
      start: draft.start || "00:00",
      end: draft.end || "23:59",
      timezone: draft.timezone.trim()
    }],
    reason: draft.reason.trim(),
    enabled: true
  };
}

export function mapPermissionPolicy(policy: BackendPermissionPolicy): PermissionPolicy {
  return {
    rules: (policy.rules ?? []).map(mapPermissionRule),
    updatedAt: policy.updated_at
  };
}

function mapPermissionRule(rule: BackendPermissionRule): PermissionRule {
  const firstWindow = rule.time_window ? [rule.time_window] : [];
  return {
    id: String(rule.id ?? crypto.randomUUID()),
    name: String(rule.name ?? ""),
    effect: rule.effect === "allow" ? "allow" : "deny",
    tools: (rule.tools ?? (rule.tool ? [rule.tool] : [])).map(String),
    pathPatterns: (rule.path_patterns ?? (rule.path_pattern ? [rule.path_pattern] : [])).map(String),
    timeWindows: [...firstWindow, ...(rule.time_windows ?? [])].map((window) => ({
      days: Array.isArray(window.days) ? window.days.map(Number).filter((day) => Number.isInteger(day)) : [],
      start: String(window.start ?? "00:00"),
      end: String(window.end ?? "23:59"),
      timezone: window.timezone ? String(window.timezone) : ""
    })),
    reason: String(rule.reason ?? ""),
    enabled: rule.enabled !== false
  };
}

function parsePermissionDays(value: string): number[] {
  const tokens = splitSettingList(value.replace(/,/g, ";")).map((item) => item.toLowerCase());
  const days = new Set<number>();
  for (const token of tokens) {
    if (token === "weekend") {
      days.add(5);
      days.add(6);
    } else if (token === "weekday") {
      [0, 1, 2, 3, 4].forEach((day) => days.add(day));
    } else if (PERMISSION_DAY_NAMES[token] !== undefined) {
      days.add(PERMISSION_DAY_NAMES[token]);
    } else {
      const numeric = Number(token);
      if (Number.isInteger(numeric) && numeric >= 0 && numeric <= 6) days.add(numeric);
    }
  }
  return Array.from(days).sort();
}

const PERMISSION_DAY_NAMES: Record<string, number> = {
  mon: 0,
  monday: 0,
  tue: 1,
  tuesday: 1,
  wed: 2,
  wednesday: 2,
  thu: 3,
  thursday: 3,
  fri: 4,
  friday: 4,
  sat: 5,
  saturday: 5,
  sun: 6,
  sunday: 6
};

function formatTimeWindow(window: PermissionTimeWindow): string {
  const days = window.days.length ? window.days.join(",") : "每天";
  return `${days} ${window.start}-${window.end}${window.timezone ? ` ${window.timezone}` : ""}`;
}

function splitSettingList(value: string) {
  return value
    .replace(/\n/g, ";")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean);
}
