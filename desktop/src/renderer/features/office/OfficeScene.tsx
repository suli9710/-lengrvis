import { ArrowUp, Loader2, ShieldCheck } from "lucide-react";
import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { TaskEvent, TaskPilotAction } from "../../../shared/executionTypes";
import type { ConnectionState, ViewKey } from "../../store";
import { useUiPreferences } from "../../lib/uiPreferences";
import {
  activeOfficeAgentIds,
  createOfficeAgentState,
  officeAgentIdForTask,
  officeViewBox,
  shouldRefreshOfficeAgentRuntime,
  type OfficeAgentDefinition,
  type OfficeAgentRuntime,
  type OfficeMapSize
} from "./model";
import { OfficeInspector } from "./OfficeInspector";
import {
  OfficeAgent,
  OfficeLayout,
  resolveOfficeAgentRuntime
} from "./OfficeStage";
import { deriveOfficeTaskPresentation } from "./officeTaskPresentation";

interface OfficeQuickSkillBase {
  id: string;
  title: string;
  summary: string;
  trust: {
    local: string;
    cloud: string;
    approval: string;
    rollback: string;
    estimate: string;
  };
  wizard: {
    input: string;
    preflight: string;
    output: string;
    nextStep: string;
  };
}

export type OfficeQuickSkill =
  | (OfficeQuickSkillBase & {
      kind: "prompt";
      prompt: string;
    })
  | (OfficeQuickSkillBase & {
      kind: "view";
      view: ViewKey;
    });

export interface HomeReadinessItem {
  id: "connection" | "privacy" | "scope" | "document";
  label: string;
  detail: string;
  state: "ready" | "action" | "warning";
  actionLabel: string;
  targetView?: ViewKey;
}

export interface HomeTrustItem {
  id: "ai" | "files" | "upload" | "approval";
  label: string;
  value: string;
  detail: string;
  state: "ready" | "warning" | "blocked";
}

export interface OfficeSceneProps {
  agents: OfficeAgentDefinition[];
  draft: string;
  recentTasks: TaskEvent[];
  quickSkills: OfficeQuickSkill[];
  readinessItems: HomeReadinessItem[];
  trustItems: HomeTrustItem[];
  activeAgentId: string;
  connectionState: ConnectionState;
  isSubmitting: boolean;
  submitError: string | null;
  onOpenApprovals: () => void;
  onDraftChange: (value: string) => void;
  onSubmitPrompt: () => void;
  onAgentSelect: (prompt: string) => void;
  onQuickSkill: (skill: OfficeQuickSkill) => void;
  onReadinessAction: (item: HomeReadinessItem) => void;
  onTaskPilotAction?: (task: TaskEvent | null, action: TaskPilotAction) => void | Promise<void>;
  pendingApprovalCount: number;
  safetyAlert: boolean;
}

type CommandPreviewStep = readonly [
  "understand" | "guard" | "execute",
  string,
  string,
  "idle" | "active" | "ready" | "blocked"
];

type CommandPreviewIntent = OfficeQuickSkill["id"] | null;
type AgentFeedbackKind = "selected" | "completed" | "failed" | "approval";

export function OfficeScene({
  agents,
  draft,
  recentTasks,
  quickSkills,
  readinessItems,
  trustItems,
  activeAgentId,
  connectionState,
  isSubmitting,
  submitError,
  onOpenApprovals,
  onDraftChange,
  onSubmitPrompt,
  onAgentSelect,
  onQuickSkill,
  onReadinessAction,
  onTaskPilotAction,
  pendingApprovalCount,
  safetyAlert
}: OfficeSceneProps) {
  const { effectiveMotion } = useUiPreferences();
  const initialOfficeAgentId = activeAgentId || "pm";
  const officeMapRef = useRef<HTMLDivElement | null>(null);
  const syncedActiveAgentIdRef = useRef(initialOfficeAgentId);
  const [officeMapSize, setOfficeMapSize] = useState<OfficeMapSize>({ width: 0, height: 0 });
  const [workingAgentId, setWorkingAgentId] = useState<string>(initialOfficeAgentId);
  const workingAgentIds = useMemo(
    () => activeOfficeAgentIds(workingAgentId, recentTasks, safetyAlert, isSubmitting),
    [isSubmitting, recentTasks, safetyAlert, workingAgentId]
  );
  const [agentState, setAgentState] = useState<Record<string, OfficeAgentRuntime>>(() =>
    createOfficeAgentState(
      agents,
      activeOfficeAgentIds(initialOfficeAgentId, recentTasks, safetyAlert, isSubmitting),
      true
    )
  );
  const [agentFeedback, setAgentFeedback] = useState<{ agentId: string; kind: AgentFeedbackKind } | null>(null);
  const [pageVisible, setPageVisible] = useState(() => typeof document === "undefined" || !document.hidden);
  const [isNarrowOffice, setIsNarrowOffice] = useState(() =>
    typeof window !== "undefined" && window.matchMedia?.("(max-width: 720px)").matches
  );
  const feedbackClearIdRef = useRef<number | undefined>(undefined);
  const feedbackFrameIdRef = useRef<number | undefined>(undefined);
  const previousTaskStatesRef = useRef<Record<string, TaskEvent["state"]>>({});
  const previousSafetyAlertRef = useRef(safetyAlert);
  const commandInputRef = useRef<HTMLTextAreaElement | null>(null);
  const didSyncWorkingAgentsRef = useRef(false);
  const [quickSkillNotice, setQuickSkillNotice] = useState("");
  const [quickSkillIntent, setQuickSkillIntent] = useState<CommandPreviewIntent>(null);

  const clearFeedbackTimer = useCallback(() => {
    if (feedbackFrameIdRef.current !== undefined) {
      window.cancelAnimationFrame(feedbackFrameIdRef.current);
      feedbackFrameIdRef.current = undefined;
    }
    if (feedbackClearIdRef.current !== undefined) {
      window.clearTimeout(feedbackClearIdRef.current);
      feedbackClearIdRef.current = undefined;
    }
  }, []);

  const triggerAgentFeedback = useCallback((agentId: string, kind: AgentFeedbackKind, persistent = false) => {
    clearFeedbackTimer();
    setAgentFeedback(null);
    feedbackFrameIdRef.current = window.requestAnimationFrame(() => {
      feedbackFrameIdRef.current = undefined;
      setAgentFeedback({ agentId, kind });
      if (!persistent) {
        feedbackClearIdRef.current = window.setTimeout(() => {
          setAgentFeedback((current) => current?.agentId === agentId && current.kind === kind ? null : current);
          feedbackClearIdRef.current = undefined;
        }, kind === "completed" ? 1100 : 820);
      }
    });
  }, [clearFeedbackTimer]);

  const refreshAgentState = useCallback((nextWorkingAgentIds: ReadonlySet<string>, refreshOneIdleAgent: boolean) => {
    setAgentState((current) => {
      const sampled = createOfficeAgentState(agents, nextWorkingAgentIds, true);
      const idleCandidates = refreshOneIdleAgent
        ? agents.filter((agent) => !nextWorkingAgentIds.has(agent.id))
        : [];
      const idleAgentId = idleCandidates.length
        ? idleCandidates[Math.floor(Math.random() * idleCandidates.length)]?.id
        : undefined;
      const next = Object.fromEntries(
        agents.map((agent) => [
          agent.id,
          shouldRefreshOfficeAgentRuntime(
            current[agent.id],
            nextWorkingAgentIds.has(agent.id),
            agent.id === idleAgentId
          )
            ? sampled[agent.id]
            : current[agent.id] ?? sampled[agent.id]
        ])
      ) as Record<string, OfficeAgentRuntime>;
      return next;
    });
  }, [agents]);

  useEffect(() => {
    const element = officeMapRef.current;
    if (!element) return;

    const updateMapSize = () => {
      const rect = element.getBoundingClientRect();
      setOfficeMapSize({ width: rect.width, height: rect.height });
    };

    updateMapSize();
    const resizeObserver = new ResizeObserver(updateMapSize);
    resizeObserver.observe(element);
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const handleVisibilityChange = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const query = window.matchMedia("(max-width: 720px)");
    const handleChange = (event: MediaQueryListEvent) => setIsNarrowOffice(event.matches);
    setIsNarrowOffice(query.matches);
    query.addEventListener?.("change", handleChange);
    return () => query.removeEventListener?.("change", handleChange);
  }, []);

  const idleMotionAllowed = effectiveMotion === "full" && pageVisible && !isNarrowOffice && officeMapSize.width > 0 && officeMapSize.height > 0;

  useEffect(() => {
    if (!idleMotionAllowed) return undefined;

    const intervalId = window.setInterval(() => {
      refreshAgentState(workingAgentIds, true);
    }, 18000);

    return () => window.clearInterval(intervalId);
  }, [idleMotionAllowed, refreshAgentState, workingAgentIds]);

  useEffect(() => () => {
    clearFeedbackTimer();
  }, [clearFeedbackTimer]);

  useEffect(() => {
    if (!didSyncWorkingAgentsRef.current) {
      didSyncWorkingAgentsRef.current = true;
      return;
    }
    refreshAgentState(workingAgentIds, false);
  }, [refreshAgentState, workingAgentIds]);

  useEffect(() => {
    if (activeAgentId && activeAgentId !== syncedActiveAgentIdRef.current) {
      syncedActiveAgentIdRef.current = activeAgentId;
      setWorkingAgentId(activeAgentId);
    }
  }, [activeAgentId]);

  useEffect(() => {
    const previous = previousTaskStatesRef.current;
    const transitioned = [...recentTasks]
      .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))
      .find((task) => {
        const before = previous[task.id];
        return before && before !== task.state && ["completed", "failed", "denied", "cancelled", "rolled_back", "repair_required"].includes(task.state);
      });

    previousTaskStatesRef.current = Object.fromEntries(recentTasks.map((task) => [task.id, task.state]));

    const transitionedAgentId = transitioned ? officeAgentIdForTask(transitioned) || workingAgentId : workingAgentId;
    if (transitioned?.state === "completed" || transitioned?.state === "rolled_back") {
      triggerAgentFeedback(transitionedAgentId, "completed");
    } else if (transitioned?.state === "failed" || transitioned?.state === "denied" || transitioned?.state === "repair_required") {
      triggerAgentFeedback(transitionedAgentId, "failed", true);
    } else if (recentTasks.some((task) => task.state === "running" || task.state === "queued")) {
      setAgentFeedback((current) => current?.kind === "failed" ? null : current);
    }
  }, [recentTasks, triggerAgentFeedback, workingAgentId]);

  useEffect(() => {
    const wasAlerting = previousSafetyAlertRef.current;
    previousSafetyAlertRef.current = safetyAlert;
    if (!wasAlerting && safetyAlert) {
      triggerAgentFeedback("safety", "approval");
    } else if (wasAlerting && !safetyAlert) {
      setAgentFeedback((current) => current?.kind === "approval" ? null : current);
    }
  }, [safetyAlert, triggerAgentFeedback]);

  const activateAgent = (agent: OfficeAgentDefinition) => {
    if (draft.trim()) {
      setQuickSkillNotice("输入框已有草稿，请先发送或清空后再选择助手。");
      commandInputRef.current?.focus();
      return;
    }
    setWorkingAgentId(agent.id);
    triggerAgentFeedback(agent.id, "selected");
    onAgentSelect(agent.prompt);
  };

  const handleQuickSkillClick = (skill: OfficeQuickSkill) => {
    if (isSubmitting) return;
    if (skill.kind === "view") {
      onQuickSkill(skill);
      return;
    }
    if (draft.trim() && (selectedQuickSkill?.kind !== "prompt" || draft !== selectedQuickSkill.prompt)) {
      setQuickSkillNotice("输入框已有草稿，请先发送或清空后再选择模板。");
      commandInputRef.current?.focus();
      return;
    }
    const responseAgentId = officeAgentForSkill(skill.id);
    setQuickSkillIntent(skill.id);
    setWorkingAgentId(responseAgentId);
    triggerAgentFeedback(responseAgentId, "selected");

    onQuickSkill(skill);
    setQuickSkillNotice(`已选择「${skill.title}」，下一步点“发送”开始。`);
    window.requestAnimationFrame(() => commandInputRef.current?.focus());
  };

  const draftReady = draft.trim().length > 0;
  const selectedQuickSkill = quickSkills.find((skill) => skill.id === quickSkillIntent) ?? null;
  const canSubmit = !isSubmitting && draftReady;
  const commandNote = commandFooterNote({
    isSubmitting,
    connectionState,
    quickSkillNotice,
    submitError
  });
  const commandNoteTone = submitError || connectionState === "offline" ? "warning" : quickSkillNotice || isSubmitting || selectedQuickSkill ? "ready" : "";
  const commandPreviewSteps = useMemo(
    () => buildCommandPreviewSteps(draft, quickSkillNotice, safetyAlert, quickSkillIntent),
    [draft, quickSkillIntent, quickSkillNotice, safetyAlert]
  );
  const commandButtonLabel = isSubmitting ? "启动中" : "发送";
  const commandPreviewReady = draftReady || Boolean(selectedQuickSkill);
  const CommandIcon = isSubmitting ? Loader2 : ArrowUp;
  const runPrimaryCommand = () => {
    if (!canSubmit) return;
    onSubmitPrompt();
  };
  const taskPresentation = useMemo(
    () => deriveOfficeTaskPresentation({
      tasks: recentTasks,
      hasDraft: draftReady,
      readinessItems,
      trustItems,
      pendingApprovalCount,
      selectedSkill: selectedQuickSkill
    }),
    [draftReady, pendingApprovalCount, readinessItems, recentTasks, selectedQuickSkill, trustItems]
  );
  const isOfficeMapReady = officeMapSize.width > 0 && officeMapSize.height > 0;
  const officeScale = isOfficeMapReady
    ? Math.min(officeMapSize.width / officeViewBox.width, officeMapSize.height / officeViewBox.height)
    : 1;
  const agentVisualScale = isOfficeMapReady ? Math.min(1, Math.max(0.56, officeScale / 0.58)) : 1;
  const officeMapStyle = { "--office-scale": officeScale } as CSSProperties;
  const primaryOfficeAgentId = safetyAlert ? "safety" : agentFeedback?.agentId ?? workingAgentId;
  const renderedAgents = isNarrowOffice
    ? agents.filter((agent) => agent.id === primaryOfficeAgentId)
    : agents;

  return (
    <div className="office-workspace" aria-label="Lengrvis 办公室">
      <div className="office-stage">
        <div className="office-headline">
          <div className="office-headline__title">
            <h2>今天，交给冷维斯什么任务？</h2>
          </div>
        </div>

        <div className="office-command-dock">
          <label className="visually-hidden" htmlFor="office-command-input">任务目标</label>
          <textarea
            id="office-command-input"
            ref={commandInputRef}
            value={draft}
            disabled={isSubmitting}
            onChange={(event) => {
              setQuickSkillIntent(null);
              setQuickSkillNotice("");
              onDraftChange(event.target.value);
            }}
            onKeyDown={(event) => {
              if (!event.nativeEvent.isComposing && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                runPrimaryCommand();
              }
            }}
            placeholder="描述你想完成的事，例如：找出下载目录里的大文件。"
            aria-describedby="office-command-safety office-command-status"
          />
          <div className="command-footer">
            <span className="command-shortcut">Ctrl / ⌘ + Enter 发送 · Enter 换行</span>
            <button
              className="button button--primary command-footer__send"
              onClick={runPrimaryCommand}
              type="button"
              disabled={!canSubmit}
              aria-busy={isSubmitting}
            >
              <CommandIcon className={isSubmitting ? "spin-icon" : undefined} size={16} aria-hidden="true" />
              {commandButtonLabel}
            </button>
          </div>
          <div id="office-command-status"
            className={`command-footer__note ${commandNoteTone ? `command-footer__note--${commandNoteTone}` : ""}`}
            role={submitError ? "alert" : "status"}>
            {commandNote}
          </div>
        </div>
        <p className="command-safety" id="office-command-safety">
          <ShieldCheck size={14} aria-hidden="true" />重要修改前会先征得你的确认。
        </p>
      </div>

      <OfficeInspector
        quickSkills={quickSkills}
        selectedQuickSkill={selectedQuickSkill}
        readinessItems={readinessItems}
        trustItems={trustItems}
        presentation={taskPresentation}
        pendingApprovalCount={pendingApprovalCount}
        safetyAlert={safetyAlert}
        onOpenApprovals={onOpenApprovals}
        onQuickSkillClick={handleQuickSkillClick}
        onReadinessAction={onReadinessAction}
        onTaskPilotAction={onTaskPilotAction}
      >
      <details className="command-guidance"><summary>任务处理说明</summary>
          <div className={commandPreviewReady ? "command-preview command-preview--ready" : "command-preview"} aria-label="执行预览">
            {commandPreviewSteps.map(([id, label, detail, state], index) => (
              <span key={id} className={`command-preview__step command-preview__step--${state}`}>
                <b className="command-preview__index" aria-hidden="true">{index + 1}</b>
                <span>
                  <strong>{label}</strong>
                  <em>{detail}</em>
                </span>
              </span>
            ))}
          </div>
      </details>
      <details className="office-team">
        <summary>小马办公室 <span>查看协作伙伴</span></summary>
        <div className="office-map" ref={officeMapRef} style={officeMapStyle}>
          <OfficeLayout
            workingAgentIds={workingAgentIds}
            motionVisible={pageVisible && isOfficeMapReady && !isNarrowOffice}
          />

          <span className="office-zone-label office-zone-label--pantry">茶水区</span>
          <span className="office-zone-label office-zone-label--gym">专注区</span>
          <span className="office-zone-label office-zone-label--lounge">休息区</span>
          <span className="office-zone-label office-zone-label--restroom">隐私区</span>
          <span className="office-zone-label office-zone-label--workstations">工位区</span>
          <span className="office-zone-label office-zone-label--meeting">计划板</span>
          <div className={`office-patrol-scan ${safetyAlert ? "office-patrol-scan--active" : ""}`} />

          <div className={`office-agents ${isOfficeMapReady ? "office-agents--ready" : ""}`}>
            {isOfficeMapReady
              ? renderedAgents.map((agent) => {
                  const isPrimary = agent.id === primaryOfficeAgentId;
                  return (
                    <OfficeAgent
                      key={agent.id}
                      agent={agent}
                      state={resolveOfficeAgentRuntime(agent, agentState[agent.id], agentState, workingAgentIds)}
                      mapSize={officeMapSize}
                      agentScale={agentVisualScale}
                      isWorking={workingAgentIds.has(agent.id)}
                      isPrimary={isPrimary}
                      motionVisible={pageVisible && isOfficeMapReady && (!isNarrowOffice || isPrimary)}
                      feedback={agentFeedback?.agentId === agent.id ? agentFeedback.kind : undefined}
                      onSelect={() => activateAgent(agent)}
                    />
                  );
                })
              : null}
          </div>
        </div>
      </details>
      </OfficeInspector>
    </div>
  );
}

function officeAgentForSkill(skillId: string): string {
  if (/document|file|download|large/i.test(skillId)) return "file";
  if (/computer|system|device/i.test(skillId)) return "computer";
  if (/browser|web/i.test(skillId)) return "browser";
  if (/search/i.test(skillId)) return "search";
  if (/app/i.test(skillId)) return "app";
  return "pm";
}

function buildCommandPreviewSteps(
  draft: string,
  notice: string,
  safetyAlert: boolean,
  intent: CommandPreviewIntent
): CommandPreviewStep[] {
  const hasDraft = draft.trim().length > 0;
  const hasQuickNotice = notice.trim().length > 0;

  if (intent === "find-large-files") {
    return [
      ["understand", "理解目标", "查找大文件", "ready"],
      ["guard", "确认范围", safetyAlert ? "需要你批准" : "发送后先选文件夹", safetyAlert ? "blocked" : "active"],
      ["execute", "执行反馈", "清理前会确认", hasDraft ? "active" : "idle"]
    ];
  }

  if (intent === "summarize-document" || intent === "document-qa") {
    return [
      ["understand", "打开文档区", intent === "document-qa" ? "选文件后提问" : "选文件后总结", "ready"],
      ["guard", "选择文档", "只读所选文件", "active"],
      ["execute", intent === "document-qa" ? "带来源回答" : "生成摘要", "打开工具后继续", "active"]
    ];
  }

  if (intent === "check-computer") {
    return [
      ["understand", "只读读取", "读取系统快照", "ready"],
      ["guard", "不改设置", "不会动配置", "ready"],
      ["execute", "发送后启动", "只读任务流程", "active"]
    ];
  }

  return [
    ["understand", "理解目标", hasDraft || hasQuickNotice ? "已准备拆解" : "等待输入", hasDraft || hasQuickNotice ? "ready" : "idle"],
    ["guard", "确认范围", safetyAlert ? "需要你批准" : hasDraft ? "先看权限" : "不改系统", safetyAlert ? "blocked" : hasDraft ? "active" : "idle"],
    ["execute", "执行反馈", hasDraft ? "实时显示进度" : "任务会留痕", hasDraft ? "active" : "idle"]
  ];
}

function commandFooterNote({
  isSubmitting,
  connectionState,
  quickSkillNotice,
  submitError
}: {
  isSubmitting: boolean;
  connectionState: ConnectionState;
  quickSkillNotice: string;
  submitError: string | null;
}): string {
  if (submitError) return submitError;
  if (isSubmitting) return "正在启动任务，返回结果前不会重复创建。";
  if (connectionState === "checking") return "正在连接助手，你可以先写好任务。";
  if (connectionState === "offline") return "连接仍在恢复；发送失败时会保留输入。";
  if (quickSkillNotice) return quickSkillNotice;
  return "";
}
