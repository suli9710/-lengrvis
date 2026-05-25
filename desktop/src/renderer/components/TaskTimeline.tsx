import { CheckCircle2, ChevronLeft, ChevronRight, Clock, Images, Pause, Play, RotateCcw, X, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { TaskEvent, TaskState, TaskStepRecording } from "../../shared/types";
import { MavrisApiClient } from "../lib/apiClient";
import { zhAgentName, zhRelativeTime, zhTaskState } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface TaskTimelineProps {
  tasks: TaskEvent[];
  api?: MavrisApiClient;
}

export function TaskTimeline({ tasks, api }: TaskTimelineProps) {
  const [previewTaskId, setPreviewTaskId] = useState<string | null>(null);
  const [previewSteps, setPreviewSteps] = useState<unknown[]>([]);
  const [recordingPlayer, setRecordingPlayer] = useState<{
    taskTitle: string;
    recording: TaskStepRecording;
    frameIndex: number;
  } | null>(null);
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
      setFeedback(response.error?.message ?? "预览失败");
    }
  };

  const executeRollback = async () => {
    if (!api || !previewTaskId) return;
    setIsWorking(true);
    const response = await api.executeRollback(previewTaskId);
    setIsWorking(false);
    if (response.ok && response.data) {
      setFeedback(`已回滚 ${response.data.count ?? 0} 个动作`);
      setPreviewTaskId(null);
      setPreviewSteps([]);
    } else {
      setFeedback(response.error?.message ?? "回滚失败");
    }
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
          <li className="timeline__item" key={task.id}>
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
              <p className="muted">将按倒序逆向执行以下动作。需要用户手动恢复（如回收站）的动作会标记。</p>
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
