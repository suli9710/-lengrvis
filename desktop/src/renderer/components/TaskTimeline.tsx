import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  HelpCircle,
  Images,
  Pause,
  Play,
  RotateCcw,
  X,
  XCircle
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type {
  CleanupPlan,
  TaskBoundaryEvent,
  TaskCompletionEvidenceStatus,
  TaskEvent,
  TaskExplain,
  TaskExplainEvidence,
  TaskState,
  TaskStepRecording
} from "../../shared/types";
import { LengrvisApiClient } from "../lib/apiClient";
import { motionAwareScrollBehavior } from "../lib/motion";
import {
  zhAgentName,
  zhBackendTaskStatus,
  zhRelativeTime,
  zhRiskLevel,
  zhSafetyVerdict,
  zhTaskState,
  zhToolName
} from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface TaskTimelineProps {
  tasks: TaskEvent[];
  api?: LengrvisApiClient;
  focusedTaskId?: string | null;
}

export function TaskTimeline({ tasks, api, focusedTaskId }: TaskTimelineProps) {
  const focusedTaskRef = useRef<HTMLLIElement | null>(null);
  const [previewTaskId, setPreviewTaskId] = useState<string | null>(null);
  const [previewSteps, setPreviewSteps] = useState<unknown[]>([]);
  const [recordingPlayer, setRecordingPlayer] = useState<{
    taskTitle: string;
    recording: TaskStepRecording;
    frameIndex: number;
  } | null>(null);
  const [explainTaskId, setExplainTaskId] = useState<string | null>(null);
  const [explain, setExplain] = useState<TaskExplain | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isWorking, setIsWorking] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const playerFrames = useMemo(
    () => recordingPlayer?.recording.frames.filter((frame) => frame.url) ?? [],
    [recordingPlayer]
  );
  const activeFrame = recordingPlayer ? playerFrames[recordingPlayer.frameIndex] : undefined;

  useEffect(() => {
    if (!isPlaying || playerFrames.length <= 1) return undefined;
    const timer = window.setInterval(() => {
      setRecordingPlayer((current) => {
        if (!current) return current;
        return {
          ...current,
          frameIndex: (current.frameIndex + 1) % playerFrames.length
        };
      });
    }, 1200);
    return () => window.clearInterval(timer);
  }, [isPlaying, playerFrames.length]);

  useEffect(() => {
    if (!focusedTaskId || !focusedTaskRef.current) return;
    focusedTaskRef.current.scrollIntoView({ behavior: motionAwareScrollBehavior(), block: "center" });
  }, [focusedTaskId, tasks]);

  const openPreview = async (taskId: string) => {
    if (!api) return;
    setIsWorking(true);
    setFeedback(null);
    const response = await api.previewRollback(taskId);
    setIsWorking(false);
    if (response.ok && response.data) {
      setPreviewTaskId(taskId);
      setPreviewSteps(response.data.steps ?? []);
    } else {
      setFeedback(response.error?.message ?? "回滚预览失败");
    }
  };

  const executeRollback = async () => {
    if (!api || !previewTaskId) return;
    setIsWorking(true);
    const response = await api.executeRollback(previewTaskId);
    setIsWorking(false);
    if (response.ok && response.data) {
      setFeedback(`已回滚 ${response.data.count ?? 0} 个动作。`);
      setPreviewTaskId(null);
      setPreviewSteps([]);
    } else {
      setFeedback(response.error?.message ?? "回滚失败");
    }
  };

  const openExplain = async (taskId: string) => {
    if (!api) return;
    setIsWorking(true);
    setFeedback(null);
    const response = await api.getTaskExplain(taskId);
    setIsWorking(false);
    if (response.ok && response.data) {
      setExplainTaskId(taskId);
      setExplain(response.data);
    } else {
      setFeedback(response.error?.message ?? "解释失败");
    }
  };

  const closeExplain = () => {
    setExplain(null);
    setExplainTaskId(null);
  };

  const openRecordingPlayer = (taskTitle: string, recording: TaskStepRecording, frameIndex = 0) => {
    const playableFrames = recording.frames.filter((frame) => frame.url);
    const targetFrame = recording.frames[frameIndex];
    const matchingIndex = playableFrames.findIndex((frame) => frame === targetFrame);
    const playableIndex = matchingIndex >= 0 ? matchingIndex : 0;
    setRecordingPlayer({ taskTitle, recording, frameIndex: playableIndex });
    setIsPlaying(false);
  };

  const closeRecordingPlayer = () => {
    setRecordingPlayer(null);
    setIsPlaying(false);
  };

  const stepPlayerFrame = (direction: -1 | 1) => {
    setRecordingPlayer((current) => {
      if (!current || playerFrames.length === 0) return current;
      return {
        ...current,
        frameIndex: (current.frameIndex + direction + playerFrames.length) % playerFrames.length
      };
    });
  };

  return (
    <Panel title="任务时间线" eyebrow="执行记录">
      {tasks.length ? (
        <ol className="timeline">
          {tasks.map((task) => {
            const trustManifest = buildTaskTrustManifest(task);
            const workspaceItems = buildTimelineWorkspace(task);
            return (
              <li
                className={task.id === focusedTaskId ? "timeline__item timeline__item--focused" : "timeline__item"}
                key={task.id}
                ref={task.id === focusedTaskId ? focusedTaskRef : undefined}
              >
                <span className={`timeline__marker timeline__marker--${task.state}`}>{iconForState(task.state)}</span>
                <div className="timeline__content">
                  <div className="row row--between">
                    <strong>{task.title}</strong>
                    <Badge tone={toneForState(task.state)}>{zhTaskState(task.state)}</Badge>
                  </div>
                  <p>{task.description}</p>
                  <div className="timeline-trust-manifest" aria-label="任务信任清单">
                    {trustManifest.map((item) => (
                      <span key={item.label} className={`timeline-trust-manifest__item timeline-trust-manifest__item--${item.tone}`}>
                        <strong>{item.label}</strong>
                        <em>{item.value}</em>
                      </span>
                    ))}
                  </div>
                  <div className="timeline-workspace" aria-label="Task Workspace">
                    <div className="timeline-workspace__head">
                      <strong>Task Workspace</strong>
                      <span>授权、工具、审批与接管状态</span>
                    </div>
                    <div className="timeline-workspace__grid">
                      {workspaceItems.map((item) => (
                        <span key={item.label} className={`timeline-workspace__item timeline-workspace__item--${item.tone}`}>
                          <b>{item.label}</b>
                          <em>{item.value}</em>
                        </span>
                      ))}
                    </div>
                  </div>
                {task.recordings?.length ? (
                  <div className="timeline-recordings">
                    {task.recordings.map((recording) => (
                      <div className="timeline-recording" key={recording.stepId}>
                        <div className="timeline-recording__head">
                          <span className="timeline-recording__title">
                            <Images size={14} aria-hidden="true" />
                            <span>{recording.toolName}</span>
                          </span>
                          <button
                            type="button"
                            className="icon-button icon-button--tiny"
                            onClick={() => openRecordingPlayer(task.title, recording)}
                            disabled={!recording.frames.some((frame) => frame.url)}
                            title="播放录屏"
                            aria-label="播放录屏"
                          >
                            <Play size={13} aria-hidden="true" />
                          </button>
                        </div>
                        <div className="timeline-recording__frames">
                          {recording.frames.map((frame, frameIndex) => (
                            <button
                              type="button"
                              className="timeline-frame"
                              key={`${recording.stepId}-${frame.phase}-${frame.capturedAt}`}
                              onClick={() => frame.url && openRecordingPlayer(task.title, recording, frameIndex)}
                              disabled={!frame.url}
                              title={frame.error || frame.phase}
                            >
                              {frame.url ? <img src={frame.url} alt={`${recording.toolName} ${phaseLabel(frame.phase)}`} /> : null}
                              <span>{phaseLabel(frame.phase)}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
                {task.cleanupPlan ? <TimelineCleanupPlan plan={task.cleanupPlan} /> : null}
                {task.boundaryEvents?.length ? <TimelineBoundaryEvents events={task.boundaryEvents} /> : null}
                <span className="muted">{zhAgentName(task.agent)} 更新于 {zhRelativeTime(task.updatedAt)}</span>
                {task.state === "completed" && api ? (
                  <div className="row" style={{ marginTop: 8 }}>
                    <button className="button button--ghost" onClick={() => void openExplain(task.id)} disabled={isWorking}>
                      <HelpCircle size={14} aria-hidden="true" />
                      为什么？
                    </button>
                    <button className="button button--ghost" onClick={() => void openPreview(task.id)} disabled={isWorking}>
                      <RotateCcw size={14} aria-hidden="true" />
                      回滚此任务
                    </button>
                  </div>
                ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="empty-state">暂无任务记录。真实任务启动后会出现在这里。</p>
      )}

      {feedback ? <p className="muted" style={{ marginTop: 12 }}>{feedback}</p> : null}

      {previewTaskId ? (
        <div className="modal-backdrop" role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="rollback-title">
            <header className="modal__header">
              <h2 id="rollback-title">回滚预览</h2>
              <Badge tone="warning">{previewSteps.length} 个动作</Badge>
            </header>
            <div className="modal__body">
              <p className="muted">将按倒序执行以下逆向动作。需要用户手动恢复的动作会标记出来。</p>
              <ol className="rollback-preview-list">
                {previewSteps.map((entry, index) => (
                  <li key={index}>
                    <code>{JSON.stringify(entry)}</code>
                  </li>
                ))}
              </ol>
            </div>
            <footer className="modal__footer">
              <button className="button button--ghost" onClick={() => setPreviewTaskId(null)} disabled={isWorking}>
                取消
              </button>
              <button className="button button--danger" onClick={() => void executeRollback()} disabled={isWorking}>
                <RotateCcw size={14} aria-hidden="true" />
                确认回滚
              </button>
            </footer>
          </div>
        </div>
      ) : null}

      {explain ? <ExplainDialog explain={explain} taskId={explainTaskId} onClose={closeExplain} /> : null}

      {recordingPlayer && activeFrame ? (
        <div className="modal-backdrop" role="presentation">
          <div className="modal modal--wide" role="dialog" aria-modal="true" aria-labelledby="recording-title">
            <header className="modal__header">
              <div>
                <span className="panel__eyebrow">步骤录屏</span>
                <h2 id="recording-title">{recordingPlayer.recording.toolName}</h2>
              </div>
              <div className="recording-player__header-actions">
                <Badge tone="info">{phaseLabel(activeFrame.phase)}</Badge>
                <button className="icon-button" onClick={closeRecordingPlayer} title="关闭" aria-label="关闭">
                  <X size={16} aria-hidden="true" />
                </button>
              </div>
            </header>
            <div className="modal__body">
              <div className="recording-player">
                <div className="recording-player__stage">
                  <img
                    className="recording-preview"
                    src={activeFrame.url}
                    alt={`${recordingPlayer.taskTitle} ${recordingPlayer.recording.toolName}`}
                  />
                </div>
                <div className="recording-player__controls">
                  <button
                    className="icon-button"
                    onClick={() => stepPlayerFrame(-1)}
                    disabled={playerFrames.length <= 1}
                    title="上一帧"
                    aria-label="上一帧"
                  >
                    <ChevronLeft size={16} aria-hidden="true" />
                  </button>
                  <button
                    className="icon-button"
                    onClick={() => setIsPlaying((value) => !value)}
                    disabled={playerFrames.length <= 1}
                    title={isPlaying ? "暂停" : "播放"}
                    aria-label={isPlaying ? "暂停" : "播放"}
                  >
                    {isPlaying ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
                  </button>
                  <button
                    className="icon-button"
                    onClick={() => stepPlayerFrame(1)}
                    disabled={playerFrames.length <= 1}
                    title="下一帧"
                    aria-label="下一帧"
                  >
                    <ChevronRight size={16} aria-hidden="true" />
                  </button>
                  <input
                    className="recording-player__slider"
                    type="range"
                    min={0}
                    max={Math.max(playerFrames.length - 1, 0)}
                    value={recordingPlayer.frameIndex}
                    onChange={(event) => {
                      const frameIndex = Number(event.currentTarget.value);
                      setRecordingPlayer((current) => current ? { ...current, frameIndex } : current);
                    }}
                    aria-label="录屏帧"
                  />
                  <span className="recording-player__counter">
                    {recordingPlayer.frameIndex + 1}/{playerFrames.length}
                  </span>
                </div>
                <div className="recording-player__meta">
                  <span className="muted">{new Date(activeFrame.capturedAt).toLocaleString()}</span>
                  {activeFrame.width && activeFrame.height ? (
                    <span className="muted">{activeFrame.width} x {activeFrame.height}</span>
                  ) : null}
                </div>
                <div className="recording-player__strip">
                  {playerFrames.map((frame, index) => (
                    <button
                      type="button"
                      className={`recording-player__thumb${index === recordingPlayer.frameIndex ? " recording-player__thumb--active" : ""}`}
                      key={`${frame.phase}-${frame.capturedAt}-${index}`}
                      onClick={() => setRecordingPlayer((current) => current ? { ...current, frameIndex: index } : current)}
                      title={phaseLabel(frame.phase)}
                    >
                      <img src={frame.url} alt={phaseLabel(frame.phase)} />
                      <span>{phaseLabel(frame.phase)}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <footer className="modal__footer">
              <button className="button button--ghost" onClick={closeRecordingPlayer}>
                <X size={14} aria-hidden="true" />
                关闭
              </button>
            </footer>
          </div>
        </div>
      ) : null}
    </Panel>
  );
}

function buildTaskTrustManifest(task: TaskEvent): Array<{ label: string; value: string; tone: "ready" | "warning" | "blocked" }> {
  const hasBoundaryEvents = Boolean(task.boundaryEvents?.length);
  const hasRollback = Boolean(task.cleanupPlan) || task.state === "completed";
  const needsApproval = task.state === "blocked" || hasBoundaryEvents;
  return [
    { label: "处理位置", value: "本机任务空间", tone: "ready" },
    { label: "文件范围", value: hasBoundaryEvents ? "按授权边界记录" : "遵循当前授权目录", tone: "ready" },
    { label: "云端边界", value: "按当前模式执行", tone: "warning" },
    { label: "审批", value: needsApproval ? "等待或已记录" : "暂无高风险动作", tone: needsApproval ? "blocked" : "ready" },
    { label: "回滚", value: hasRollback ? "可查看预案" : "完成后生成", tone: hasRollback ? "ready" : "warning" }
  ];
}

function buildTimelineWorkspace(task: TaskEvent): Array<{ label: string; value: string; tone: "ready" | "warning" | "blocked" }> {
  const hasApproval = task.state === "blocked" || Boolean(task.boundaryEvents?.length);
  const hasRollback = Boolean(task.cleanupPlan) || task.state === "completed";
  return [
    {
      label: "当前动作",
      value: workspaceAction(task),
      tone: task.state === "failed" ? "blocked" : task.state === "queued" ? "warning" : "ready"
    },
    {
      label: "工具权限",
      value: workspaceTool(task),
      tone: "ready"
    },
    {
      label: "审批点",
      value: hasApproval ? "等待或已记录" : "暂无待审批",
      tone: hasApproval ? "blocked" : "ready"
    },
    {
      label: "回滚/接管",
      value: hasRollback ? "可查看留痕" : task.state === "paused" ? "已暂停" : "完成后生成",
      tone: hasRollback ? "ready" : "warning"
    }
  ];
}

function workspaceAction(task: TaskEvent): string {
  if (task.state === "queued") return "等待执行";
  if (task.state === "running") return "正在执行";
  if (task.state === "blocked") return "等待审批";
  if (task.state === "paused") return "已暂停";
  if (task.state === "completed") return "已完成";
  return "需要复核";
}

function workspaceTool(task: TaskEvent): string {
  const text = `${task.title} ${task.description} ${task.agent}`.toLowerCase();
  if (task.cleanupPlan || /cleanup|清理|下载|大文件|file/.test(text)) return "文件工具";
  if (/document|文档|总结|问答/.test(text)) return "文档工具";
  if (/computer|system|电脑|系统/.test(text)) return "系统只读";
  if (/browser|网页|浏览器/.test(text)) return "浏览器工具";
  return "任务工具";
}

function TimelineBoundaryEvents({ events }: { events: TaskBoundaryEvent[] }) {
  return (
    <div className="timeline-boundary" aria-label="工程边界事件">
      {events.slice(-5).map((event) => (
        <article className="timeline-boundary__item" key={event.id}>
          <div className="row row--between">
            <strong>{event.title}</strong>
            <Badge tone={toneForBoundary(event.severity)}>{boundaryKindLabel(event.kind)}</Badge>
          </div>
          <p>{event.detail}</p>
          <span className="muted">
            {event.stepId ? `step ${event.stepId} · ` : ""}
            {zhRelativeTime(event.createdAt)}
          </span>
        </article>
      ))}
    </div>
  );
}

function TimelineCleanupPlan({ plan }: { plan: CleanupPlan }) {
  const permanent = plan.items.filter((item) => item.disposition === "permanent_delete");
  const trash = plan.items.filter((item) => item.disposition === "trash");
  const suggestions = plan.items.length - permanent.length - trash.length;
  return (
    <div className="timeline-cleanup">
      <div className="row row--between">
        <strong>清理预览</strong>
        <span className="muted">{formatBytes(plan.reclaimableBytes)} 可释放</span>
      </div>
      <div className="timeline-cleanup__counts">
        <span>永久删除 {permanent.length}</span>
        <span>进回收站 {trash.length}</span>
        <span>仅建议 {suggestions}</span>
      </div>
      {plan.riskWarnings.length ? <p>{plan.riskWarnings[0]}</p> : null}
    </div>
  );
}

function formatBytes(bytes?: number): string {
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

function ExplainDialog({ explain, taskId, onClose }: { explain: TaskExplain; taskId: string | null; onClose: () => void }) {
  const completionEvidence = explain.finalResult.completionEvidence;
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal modal--wide" role="dialog" aria-modal="true" aria-labelledby="explain-title">
        <header className="modal__header">
          <div>
            <span className="panel__eyebrow">执行解释</span>
            <h2 id="explain-title">为什么这样执行？</h2>
          </div>
          <div className="recording-player__header-actions">
            <Badge tone={explain.complete ? "success" : "warning"}>{explain.complete ? "完整链路" : "部分记录"}</Badge>
            <button className="icon-button" onClick={onClose} title="关闭" aria-label="关闭">
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        </header>
        <div className="modal__body">
          <div className="explain-summary">
            <div>
              <span className="muted">目标</span>
              <strong>{explain.userGoal}</strong>
            </div>
            <div>
              <span className="muted">状态</span>
              <Badge tone={explain.status === "completed" ? "success" : "info"}>{zhBackendTaskStatus(explain.status)}</Badge>
            </div>
            <div>
              <span className="muted">数据来源</span>
              <span>{formatSources(explain.dataSources)}</span>
            </div>
            <div>
              <span className="muted">结果证据</span>
              <Badge tone={completionEvidenceTone(completionEvidence.status)}>
                {completionEvidenceLabel(completionEvidence.status)}
              </Badge>
            </div>
          </div>

          <div className={`explain-result-evidence explain-result-evidence--${completionEvidence.status}`}>
            <div className="row row--between">
              <strong>结果证据状态</strong>
              <Badge tone={completionEvidenceTone(completionEvidence.status)}>
                {completionEvidenceLabel(completionEvidence.status)}
              </Badge>
            </div>
            <p>{completionEvidence.summary}</p>
            <span>{completionEvidence.privacyNote ?? "仅展示证据状态，不展示原始证据内容。"}</span>
          </div>

          <div className="explain-chain">
            {explain.chain.map((item) => (
              <article className="explain-chain__item" key={item.stage}>
                <span className="explain-chain__marker">{stageNumber(item.stage)}</span>
                <div>
                  <div className="row row--between">
                    <strong>{stageTitle(item.stage, item.title)}</strong>
                    <span className="muted">{item.evidence.length} 条证据</span>
                  </div>
                  <p>{item.summary}</p>
                  {item.evidence.length ? <EvidenceList evidence={item.evidence.slice(0, 3)} /> : null}
                </div>
              </article>
            ))}
          </div>

          {explain.steps.length ? (
            <div className="explain-steps">
              <strong>步骤审查</strong>
              {explain.steps.map((step) => (
                <article className="explain-step" key={step.stepId}>
                  <div className="row row--between">
                    <span>{step.order}. {zhToolName(step.toolName)}</span>
                    <Badge tone={step.requiresApproval ? "warning" : "neutral"}>{zhRiskLevel(step.riskLevel)}</Badge>
                  </div>
                  <p>{step.plannerReason || step.description}</p>
                  {step.subagentSuggestions.map((message) => (
                    <p className="muted" key={message.id}>
                      {zhAgentName(message.fromAgent)}：{message.action?.rationale || message.content}
                    </p>
                  ))}
                  {step.safetyReviews.map((review) => (
                    <p className="muted" key={review.id}>
                      安全审查 {zhSafetyVerdict(review.verdict)}：{review.reasons.join(" ")}
                    </p>
                  ))}
                </article>
              ))}
            </div>
          ) : null}
        </div>
        <footer className="modal__footer">
          <span className="muted">{taskId}</span>
          <button className="button button--ghost" onClick={onClose}>
            <X size={14} aria-hidden="true" />
            关闭
          </button>
        </footer>
      </div>
    </div>
  );
}

function EvidenceList({ evidence }: { evidence: TaskExplainEvidence[] }) {
  return (
    <ul className="explain-evidence">
      {evidence.map((item) => (
        <li key={`${item.source}-${item.id}`}>
          <span>{item.source}</span>
          <p>{item.actor ? `${zhAgentName(item.actor)}：` : ""}{item.summary}</p>
        </li>
      ))}
    </ul>
  );
}

function stageTitle(stage: string, fallback: string) {
  const labels: Record<string, string> = {
    user_goal: "用户目标",
    supervisor_judgment: "主管判断",
    planner_reasoning: "规划理由",
    step_safety_reviews: "每步安全审查",
    subagent_suggestions: "子 Agent 建议",
    final_result: "最终结果"
  };
  return labels[stage] ?? fallback;
}

function stageNumber(stage: string) {
  const order = ["user_goal", "supervisor_judgment", "planner_reasoning", "step_safety_reviews", "subagent_suggestions", "final_result"];
  const index = order.indexOf(stage);
  return index >= 0 ? index + 1 : "·";
}

function formatSources(sources: Record<string, number>) {
  return Object.entries(sources)
    .map(([name, count]) => `${name}: ${count}`)
    .join(" / ");
}

function completionEvidenceLabel(status: TaskCompletionEvidenceStatus): string {
  const labels: Record<TaskCompletionEvidenceStatus, string> = {
    unverified: "未验证",
    task_evidence_only: "仅任务证据",
    visible_progress: "可见进度",
    safe_failure: "安全失败",
    verified_completed_result: "最终结果已验证"
  };
  return labels[status];
}

function completionEvidenceTone(status: TaskCompletionEvidenceStatus): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status === "verified_completed_result") return "success";
  if (status === "safe_failure") return "danger";
  if (status === "visible_progress") return "info";
  if (status === "task_evidence_only") return "warning";
  return "neutral";
}

function boundaryKindLabel(kind: string) {
  if (kind === "model_boundary_denied") return "模型边界";
  if (kind === "context_boundary" || kind === "context_projection") return "上下文";
  if (kind === "tool_progress") return "工具进度";
  if (kind === "post_tool_review") return "工具审查";
  if (kind === "tool_contract") return "工具契约";
  return "边界";
}

function toneForBoundary(severity: string): "neutral" | "success" | "warning" | "danger" | "info" {
  if (severity === "danger" || severity === "critical" || severity === "high") return "danger";
  if (severity === "warning" || severity === "medium") return "warning";
  if (severity === "success" || severity === "low") return "success";
  return "info";
}

function phaseLabel(phase: string) {
  if (phase.includes("before")) return "执行前";
  if (phase.includes("after")) return "执行后";
  return phase || "截图";
}

function iconForState(state: TaskState) {
  if (state === "completed") {
    return <CheckCircle2 size={16} aria-hidden="true" />;
  }

  if (state === "failed" || state === "blocked") {
    return <XCircle size={16} aria-hidden="true" />;
  }

  return <Clock size={16} aria-hidden="true" />;
}

function toneForState(state: TaskState): "neutral" | "success" | "warning" | "danger" | "info" {
  switch (state) {
    case "completed":
      return "success";
    case "blocked":
      return "warning";
    case "paused":
      return "neutral";
    case "failed":
      return "danger";
    case "running":
      return "info";
    default:
      return "neutral";
  }
}
