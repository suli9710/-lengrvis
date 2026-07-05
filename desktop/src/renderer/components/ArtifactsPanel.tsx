import { FileText, FolderOpen, PackageOpen, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { TaskArtifact, TaskArtifactsSummary, TaskEvent } from "../../shared/executionTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { zhRelativeTime, zhToolName } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface ArtifactsPanelProps {
  tasks: TaskEvent[];
  api: LengrvisApiClient;
  focusedTaskId?: string | null;
  onRevealPath?: (path: string) => Promise<void>;
}

export function ArtifactsPanel({ tasks, api, focusedTaskId, onRevealPath }: ArtifactsPanelProps) {
  const candidateTasks = useMemo(() => tasks.filter((task) => task.id), [tasks]);
  const defaultTaskId = focusedTaskId ?? candidateTasks[0]?.id ?? null;
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(defaultTaskId);
  const [summary, setSummary] = useState<TaskArtifactsSummary | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revealError, setRevealError] = useState<string | null>(null);

  useEffect(() => {
    if (focusedTaskId) setSelectedTaskId(focusedTaskId);
  }, [focusedTaskId]);

  useEffect(() => {
    if (!selectedTaskId && candidateTasks[0]?.id) {
      setSelectedTaskId(candidateTasks[0].id);
    }
  }, [candidateTasks, selectedTaskId]);

  const loadArtifacts = useCallback(async (taskId: string) => {
    setIsLoading(true);
    setError(null);
    const response = await api.listTaskArtifacts(taskId);
    setIsLoading(false);
    if (response.ok && response.data) {
      setSummary(response.data);
      return;
    }
    setSummary(null);
    setError(response.error?.message ?? "无法读取任务成果物");
  }, [api]);

  useEffect(() => {
    if (!selectedTaskId) {
      setSummary(null);
      return;
    }
    void loadArtifacts(selectedTaskId);
  }, [loadArtifacts, selectedTaskId]);

  const revealArtifact = async (artifact: TaskArtifact) => {
    if (!onRevealPath) return;
    setRevealError(null);
    try {
      await onRevealPath(artifact.path);
    } catch (revealFailure) {
      setRevealError(revealFailure instanceof Error ? revealFailure.message : "无法打开所在位置");
    }
  };

  return (
    <Panel title="任务成果物" eyebrow="工作台">
      <div className="artifact-panel__toolbar">
        <select
          className="artifact-panel__select"
          value={selectedTaskId ?? ""}
          onChange={(event) => setSelectedTaskId(event.currentTarget.value || null)}
          aria-label="选择任务"
        >
          {!candidateTasks.length ? <option value="">暂无任务</option> : null}
          {candidateTasks.map((task) => (
            <option key={task.id} value={task.id}>
              {task.title.length > 42 ? `${task.title.slice(0, 42)}…` : task.title}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="icon-button"
          onClick={() => selectedTaskId && void loadArtifacts(selectedTaskId)}
          disabled={!selectedTaskId || isLoading}
          title="刷新成果物"
          aria-label="刷新成果物"
        >
          <RefreshCw size={14} aria-hidden="true" className={isLoading ? "spin-icon" : undefined} />
        </button>
      </div>

      {error ? <p className="empty-state">{error}</p> : null}

      {!error && summary ? (
        summary.artifacts.length ? (
          <>
            <div className="artifact-panel__counts">
              <Badge tone="info">共 {summary.counts.total}</Badge>
              <Badge tone="success">生成 {summary.counts.generated}</Badge>
              <Badge tone="neutral">改动 {summary.counts.changed}</Badge>
              {summary.counts.missing ? <Badge tone="warning">已不存在 {summary.counts.missing}</Badge> : null}
            </div>
            <ul className="artifact-list">
              {summary.artifacts.map((artifact) => (
                <li className={artifact.exists ? "artifact-list__item" : "artifact-list__item artifact-list__item--missing"} key={artifact.path}>
                  <span className="artifact-list__icon" aria-hidden="true">
                    {artifact.isDir ? <FolderOpen size={15} /> : <FileText size={15} />}
                  </span>
                  <div className="artifact-list__body">
                    <strong title={artifact.path}>{artifactName(artifact.path)}</strong>
                    <span className="muted">
                      {artifact.kind === "output" ? "任务生成" : "任务改动"}
                      {artifact.toolName ? ` · ${zhToolName(artifact.toolName)}` : ""}
                      {artifact.exists && !artifact.isDir && artifact.sizeBytes ? ` · ${formatArtifactSize(artifact.sizeBytes)}` : ""}
                      {!artifact.exists ? " · 已不存在" : ""}
                      {artifact.createdAt ? ` · ${zhRelativeTime(artifact.createdAt)}` : ""}
                    </span>
                  </div>
                  {onRevealPath && artifact.exists ? (
                    <button
                      type="button"
                      className="icon-button icon-button--tiny"
                      onClick={() => void revealArtifact(artifact)}
                      title="打开所在位置"
                      aria-label="打开所在位置"
                    >
                      <FolderOpen size={13} aria-hidden="true" />
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="empty-state">
            <PackageOpen size={16} aria-hidden="true" /> 这个任务还没有产出文件成果物。生成报告或改动文件后会出现在这里。
          </p>
        )
      ) : null}

      {!error && !summary && !isLoading ? (
        <p className="empty-state">选择一个任务即可查看它生成或改动的文件。</p>
      ) : null}

      {revealError ? <p className="muted">{revealError}</p> : null}
    </Panel>
  );
}

function artifactName(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const name = normalized.split("/").filter(Boolean).at(-1);
  return name || path;
}

function formatArtifactSize(bytes: number): string {
  if (!bytes || !Number.isFinite(bytes)) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}
