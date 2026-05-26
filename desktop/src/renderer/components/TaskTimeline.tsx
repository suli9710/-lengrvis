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

import type { TaskEvent, TaskExplain, TaskExplainEvidence, TaskState, TaskStepRecording } from "../../shared/types";
import { MavrisApiClient } from "../lib/apiClient";
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
  api?: MavrisApiClient;
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
    focusedTaskRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
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
      setFeedback(response.error?.message ?? "Rollback preview failed");
    }
  };

  const executeRollback = async () => {
    if (!api || !previewTaskId) return;
    setIsWorking(true);
    const response = await api.executeRollback(previewTaskId);
    setIsWorking(false);
    if (response.ok && response.data) {
      setFeedback(`Rolled back ${response.data.count ?? 0} action(s).`);
      setPreviewTaskId(null);
      setPreviewSteps([]);
    } else {
      setFeedback(response.error?.message ?? "Rollback failed");
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
      setFeedback(response.error?.message ?? "Explain failed");
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
      <ol className="timeline">
        {tasks.map((task) => (
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
        ))}
      </ol>

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
                <span className="panel__eyebrow">Step 录屏</span>
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

function ExplainDialog({ explain, taskId, onClose }: { explain: TaskExplain; taskId: string | null; onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal modal--wide" role="dialog" aria-modal="true" aria-labelledby="explain-title">
        <header className="modal__header">
          <div>
            <span className="panel__eyebrow">Explain API</span>
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
    case "failed":
      return "danger";
    case "running":
      return "info";
    default:
      return "neutral";
  }
}
