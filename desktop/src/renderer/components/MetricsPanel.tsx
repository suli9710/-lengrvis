import { Activity, RefreshCw, ShieldQuestion } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import type { LocalMetricsSummary } from "../../shared/systemTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { Badge, Panel } from "./Panel";

interface MetricsPanelProps {
  api: LengrvisApiClient;
}

const WINDOW_OPTIONS = [7, 14, 30] as const;

export function MetricsPanel({ api }: MetricsPanelProps) {
  const [windowDays, setWindowDays] = useState<number>(7);
  const [summary, setSummary] = useState<LocalMetricsSummary | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [optInRequired, setOptInRequired] = useState(false);

  const loadMetrics = useCallback(
    async (days: number) => {
      setIsLoading(true);
      setError(null);
      setOptInRequired(false);
      const response = await api.getLocalMetrics(days);
      setIsLoading(false);
      if (response.ok && response.data) {
        setSummary(response.data);
        return;
      }
      setSummary(null);
      if (response.status === 403) {
        setOptInRequired(true);
        return;
      }
      setError(response.error?.message ?? "无法读取本机指标");
    },
    [api]
  );

  useEffect(() => {
    void loadMetrics(windowDays);
  }, [loadMetrics, windowDays]);

  return (
    <Panel title="本机执行指标" eyebrow="可观测性">
      <div className="metrics-panel__toolbar">
        <select
          className="metrics-panel__select"
          value={windowDays}
          onChange={(event) => setWindowDays(Number(event.currentTarget.value) || 7)}
          aria-label="统计窗口"
        >
          {WINDOW_OPTIONS.map((days) => (
            <option key={days} value={days}>
              最近 {days} 天
            </option>
          ))}
        </select>
        <button
          type="button"
          className="icon-button"
          onClick={() => void loadMetrics(windowDays)}
          disabled={isLoading}
          title="刷新指标"
          aria-label="刷新指标"
        >
          <RefreshCw size={14} aria-hidden="true" className={isLoading ? "spin-icon" : undefined} />
        </button>
      </div>

      {optInRequired ? (
        <p className="empty-state">
          <ShieldQuestion size={16} aria-hidden="true" /> 本机指标默认关闭。它只在这台电脑上统计计数（任务成功率、自动恢复占比等），
          不会上传任何内容。在“本机指标”设置中开启后即可查看。
        </p>
      ) : null}

      {error ? <p className="empty-state">{error}</p> : null}

      {summary ? (
        <div className="metrics-grid">
          <MetricCard
            label="任务成功率"
            value={formatRate(summary.tasks.successRate)}
            hint={`${summary.tasks.succeeded}/${summary.tasks.terminal} 个已结束任务成功`}
          />
          <MetricCard
            label="自动恢复触发率"
            value={formatRate(summary.recovery.recoveryTriggerRate)}
            hint={`${summary.recovery.runsWithReflection}/${summary.runs.total} 次运行触发了自动恢复`}
          />
          <MetricCard
            label="恢复时求助用户占比"
            value={formatRate(summary.recovery.askUserShare)}
            hint={`${summary.recovery.decidedActions["ask_user"] ?? 0} 次恢复升级为询问用户`}
          />
          <MetricCard
            label="模型异常完成率"
            value={formatRate(summary.llm.anomalyRate)}
            hint={`${summary.llm.anomalies}/${summary.llm.calls} 次模型调用异常结束`}
          />
        </div>
      ) : null}

      {summary ? (
        <div className="metrics-panel__footer">
          <Badge tone="info">
            <Activity size={12} aria-hidden="true" /> 任务 {summary.tasks.total} · 运行 {summary.runs.total} · 模型调用{" "}
            {summary.llm.calls}
          </Badge>
          <span className="muted">仅本机统计，不上传。</span>
        </div>
      ) : null}
    </Panel>
  );
}

function MetricCard({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="metric-card">
      <span className="metric-card__label">{label}</span>
      <strong className="metric-card__value">{value}</strong>
      <span className="metric-card__hint muted">{hint}</span>
    </div>
  );
}

function formatRate(rate: number | null): string {
  if (rate === null || !Number.isFinite(rate)) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}
