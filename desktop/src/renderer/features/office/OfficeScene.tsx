import {
  CheckCircle2,
  Clock,
  CornerDownLeft,
  FileText,
  FolderOpen,
  LockKeyhole,
  Radio,
  ShieldCheck,
  Sparkles,
  type LucideIcon
} from "lucide-react";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";

import type { TaskEvent } from "../../../shared/types";
import type { ConnectionState, ViewKey } from "../../store";
import xiaomaWalkingGif from "../../assets/xiaoma-agent/fc_walking_h.gif";
import xiaomaCoffeeGif from "../../assets/xiaoma-agent/fc_drink_coffee.gif";
import xiaomaScreenAppGif from "../../assets/xiaoma-agent/fc_screen_working_apk_use.gif";
import xiaomaScreenFileGif from "../../assets/xiaoma-agent/fc_screen_working_file_use.gif";
import xiaomaScreenMainGif from "../../assets/xiaoma-agent/fc_screen_working_main.gif";
import xiaomaScreenSearchGif from "../../assets/xiaoma-agent/fc_screen_working_search_or_browser_use.gif";
import xiaomaScreenComputerGif from "../../assets/xiaoma-agent/fc_screen_working_win_use.gif";
import xiaomaPoopingGif from "../../assets/xiaoma-agent/fc_pooping-0_cropped.gif";
import xiaomaTreadmillGif from "../../assets/xiaoma-agent/running_treadmill_cropped.gif";
import xiaomaSaluteGif from "../../assets/xiaoma-agent/salute.gif";
import xiaomaSleepingGif from "../../assets/xiaoma-agent/sleeping.gif";
import xiaomaStandbyGif from "../../assets/xiaoma-agent/standby.gif";
import xiaomaWorkingGif from "../../assets/xiaoma-agent/working.gif";
import officeChair from "../../assets/office-analysis/workstation-parts/chair.png";
import officeChairBoss from "../../assets/office-analysis/workstation-parts/chair_boss.png";
import officeDesk from "../../assets/office-analysis/workstation-parts/desk.png";
import officeDeskBoss from "../../assets/office-analysis/workstation-parts/desk_boss.png";
import officeScreenIdle from "../../assets/office-analysis/workstation-parts/screen_img.png";
import officeScreenOn from "../../assets/office-analysis/workstation-parts/screen_on.png";
import officeShadow from "../../assets/office-analysis/workstation-parts/shadow.png";
import officeShadowBoss from "../../assets/office-analysis/workstation-parts/shadow_boss.png";
import officeToilet from "../../assets/office-analysis/workstation-parts/toilet.png";
import officeTreadmill from "../../assets/office-analysis/workstation-parts/treadmill.png";
import officeWaterBar from "../../assets/office-analysis/workstation-parts/water_bar.png";
import {
  activeOfficeAgentIds,
  createOfficeAgentState,
  officeViewBox,
  projectOfficePoint,
  type OfficeAgentDefinition,
  type OfficeAgentPose,
  type OfficeAgentRuntime,
  type OfficeMapSize
} from "./model";
import {
  deriveOfficeTaskPresentation,
  taskDisplayState,
  taskDisplayTitle
} from "./officeTaskPresentation";

interface OfficeQuickSkillBase {
  id: string;
  icon: LucideIcon;
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
    })
  | (OfficeQuickSkillBase & {
      kind: "action";
      action: "system-check";
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
  onDraftChange: (value: string) => void;
  onSubmitPrompt: () => void;
  onAgentSelect: (prompt: string) => void;
  onQuickSkill: (skill: OfficeQuickSkill) => void;
  onReadinessAction: (item: HomeReadinessItem) => void;
  onTaskPilotAction?: (task: TaskEvent | null, action: "open" | "approve" | "compose") => void;
  pendingApprovalCount: number;
  safetyAlert: boolean;
}

interface OfficeSlot {
  agentId: string;
  x: number;
  y: number;
  boss?: boolean;
}

interface PonyAgentProps {
  accent: string;
  pose: OfficeAgentPose;
  isLead?: boolean;
  isWorking: boolean;
  isMoving: boolean;
}

interface OfficeAgentVisualMetrics {
  width: number;
  height: number;
  offsetX: number;
  offsetY: number;
  bubbleY: number;
  labelY: number;
  haloLeft: number;
  haloTop: number;
  haloWidth: number;
  haloHeight: number;
}

interface CommandPreviewStep {
  id: "understand" | "guard" | "execute";
  label: string;
  detail: string;
  state: "idle" | "active" | "ready" | "blocked";
  icon: LucideIcon;
}

type CommandPreviewIntent = OfficeQuickSkill["id"] | null;


const xiaomaPoseGifs: Record<OfficeAgentPose, string> = {
  working: xiaomaWorkingGif,
  coffee: xiaomaCoffeeGif,
  treadmill: xiaomaTreadmillGif,
  restroom: xiaomaPoopingGif,
  nap: xiaomaSleepingGif,
  wander: xiaomaWalkingGif,
  review: xiaomaSaluteGif
};

const xiaomaScreenWorkingGifs: Record<string, string> = {
  pm: xiaomaScreenMainGif,
  app: xiaomaScreenAppGif,
  file: xiaomaScreenFileGif,
  computer: xiaomaScreenComputerGif,
  browser: xiaomaScreenSearchGif,
  search: xiaomaScreenSearchGif
};

const officeSlots: OfficeSlot[] = [
  { agentId: "pm", x: 608, y: 160, boss: true },
  { agentId: "app", x: 864, y: 160 },
  { agentId: "computer", x: 608, y: 416 },
  { agentId: "browser", x: 864, y: 416 },
  { agentId: "file", x: 608, y: 672 },
  { agentId: "search", x: 864, y: 672 }
];

const xiaomaBaseVisualMetrics: Record<OfficeAgentPose, Omit<OfficeAgentVisualMetrics, "haloLeft" | "haloTop">> = {
  working: { width: 140, height: 105, offsetX: 0, offsetY: 0, bubbleY: -132, labelY: 10, haloWidth: 160, haloHeight: 116 },
  coffee: { width: 140, height: 105, offsetX: 0, offsetY: 0, bubbleY: -132, labelY: 10, haloWidth: 160, haloHeight: 116 },
  treadmill: { width: 118, height: 108, offsetX: 0, offsetY: 0, bubbleY: -134, labelY: 10, haloWidth: 132, haloHeight: 116 },
  restroom: { width: 96, height: 132, offsetX: 0, offsetY: 0, bubbleY: -156, labelY: 10, haloWidth: 118, haloHeight: 140 },
  nap: { width: 150, height: 112, offsetX: 0, offsetY: 0, bubbleY: -140, labelY: 10, haloWidth: 170, haloHeight: 122 },
  wander: { width: 140, height: 105, offsetX: 0, offsetY: 0, bubbleY: -132, labelY: 10, haloWidth: 160, haloHeight: 116 },
  review: { width: 140, height: 105, offsetX: 0, offsetY: 0, bubbleY: -132, labelY: 10, haloWidth: 160, haloHeight: 116 }
};

const safetySharedAnchorFallback = { x: 1030, y: 382 };
const sharedAnchorTolerance = 1.5;
const agentTravelDurationMs = 4200;

const agentAnchorStyle = {
  position: "absolute",
  left: 0,
  top: 0,
  overflow: "visible",
  pointerEvents: "auto"
} as CSSProperties;

const agentGroundAnchorStyle = {
  position: "absolute",
  left: 0,
  top: 0,
  width: 0,
  height: 0,
  pointerEvents: "none"
} as CSSProperties;

const agentSpriteStyle = {
  position: "absolute",
  left: "50%",
  bottom: 0,
  width: "var(--agent-visual-width)",
  height: "var(--agent-visual-height)",
  transform: "translate(calc(-50% + var(--agent-pose-offset-x)), var(--agent-pose-offset-y))",
  transformOrigin: "50% 100%",
  pointerEvents: "none"
} as CSSProperties;

const agentBubbleStyle = {
  left: "50%",
  top: "calc(100% + var(--agent-bubble-y))",
  bottom: "auto",
  translate: "-50% 0"
} as CSSProperties;

const agentLabelStyle = {
  position: "absolute",
  left: "50%",
  top: "calc(100% + var(--agent-label-y))",
  translate: "-50% 0"
} as CSSProperties;

const agentHaloStyle = {
  inset: "auto",
  left: "calc(50% + var(--agent-halo-left))",
  top: "calc(100% + var(--agent-halo-top))",
  width: "var(--agent-halo-width)",
  height: "var(--agent-halo-height)"
} as CSSProperties;

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
  onDraftChange,
  onSubmitPrompt,
  onAgentSelect,
  onQuickSkill,
  onReadinessAction,
  onTaskPilotAction,
  pendingApprovalCount,
  safetyAlert
}: OfficeSceneProps) {
  const initialOfficeAgentId = activeAgentId || "pm";
  const officeMapRef = useRef<HTMLDivElement | null>(null);
  const syncedActiveAgentIdRef = useRef(initialOfficeAgentId);
  const [officeMapSize, setOfficeMapSize] = useState<OfficeMapSize>({ width: 0, height: 0 });
  const [workingAgentId, setWorkingAgentId] = useState<string>(initialOfficeAgentId);
  const workingAgentIds = useMemo(
    () => activeOfficeAgentIds(workingAgentId, recentTasks, safetyAlert),
    [recentTasks, safetyAlert, workingAgentId]
  );
  const [agentState, setAgentState] = useState<Record<string, OfficeAgentRuntime>>(() =>
    createOfficeAgentState(agents, activeOfficeAgentIds(initialOfficeAgentId, recentTasks, safetyAlert), true)
  );
  const [movingAgents, setMovingAgents] = useState<Set<string>>(() => new Set());
  const walkClearIdRef = useRef<number | undefined>(undefined);
  const quickNoticeClearIdRef = useRef<number | undefined>(undefined);
  const quickActionLaunchIdRef = useRef<number | undefined>(undefined);
  const commandInputRef = useRef<HTMLTextAreaElement | null>(null);
  const didSyncWorkingAgentsRef = useRef(false);
  const [quickSkillNotice, setQuickSkillNotice] = useState("");
  const [quickSkillIntent, setQuickSkillIntent] = useState<CommandPreviewIntent>(null);
  const [sendHintPulse, setSendHintPulse] = useState(false);

  const refreshAgentState = (nextWorkingAgentIds: ReadonlySet<string>, refreshIdleAgents: boolean) => {
    setAgentState((current) => {
      const sampled = createOfficeAgentState(agents, nextWorkingAgentIds, true);
      const next = Object.fromEntries(
        agents.map((agent) => [
          agent.id,
          refreshIdleAgents || nextWorkingAgentIds.has(agent.id)
            ? sampled[agent.id]
            : current[agent.id] ?? sampled[agent.id]
        ])
      ) as Record<string, OfficeAgentRuntime>;
      const moving = getMovingAgentIds(agents, current, next);
      if (moving.size > 0) {
        setMovingAgents(moving);
        if (walkClearIdRef.current) window.clearTimeout(walkClearIdRef.current);
        walkClearIdRef.current = window.setTimeout(() => {
          setMovingAgents(new Set());
          walkClearIdRef.current = undefined;
        }, agentTravelDurationMs);
      }
      return next;
    });
  };

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
    const intervalId = window.setInterval(() => {
      refreshAgentState(workingAgentIds, true);
    }, 14000);

    return () => {
      window.clearInterval(intervalId);
      if (walkClearIdRef.current) {
        window.clearTimeout(walkClearIdRef.current);
        walkClearIdRef.current = undefined;
      }
      if (quickNoticeClearIdRef.current) {
        window.clearTimeout(quickNoticeClearIdRef.current);
        quickNoticeClearIdRef.current = undefined;
      }
      if (quickActionLaunchIdRef.current) {
        window.clearTimeout(quickActionLaunchIdRef.current);
        quickActionLaunchIdRef.current = undefined;
      }
    };
  }, [agents, workingAgentIds]);

  useEffect(() => {
    if (!didSyncWorkingAgentsRef.current) {
      didSyncWorkingAgentsRef.current = true;
      return;
    }
    refreshAgentState(workingAgentIds, false);
  }, [workingAgentIds]);

  useEffect(() => {
    if (activeAgentId && activeAgentId !== syncedActiveAgentIdRef.current) {
      syncedActiveAgentIdRef.current = activeAgentId;
      setWorkingAgentId(activeAgentId);
    }
  }, [activeAgentId, agents, recentTasks, safetyAlert]);

  const activateAgent = (agent: OfficeAgentDefinition) => {
    setWorkingAgentId(agent.id);
    onAgentSelect(agent.prompt);
  };

  const handleQuickSkillClick = (skill: OfficeQuickSkill) => {
    setQuickSkillIntent(skill.id);

    if (quickNoticeClearIdRef.current) {
      window.clearTimeout(quickNoticeClearIdRef.current);
    }
    if (quickActionLaunchIdRef.current) {
      window.clearTimeout(quickActionLaunchIdRef.current);
      quickActionLaunchIdRef.current = undefined;
    }
    if (skill.kind === "prompt") {
      onQuickSkill(skill);
      setQuickSkillNotice("已填好这句话，下一步点“发送”开始；清理前不会删除任何文件。");
      setSendHintPulse(false);
      window.setTimeout(() => setSendHintPulse(true), 0);
      window.setTimeout(() => setSendHintPulse(false), 1800);
      window.requestAnimationFrame(() => commandInputRef.current?.focus());
    } else if (skill.kind === "view") {
      setQuickSkillNotice(`正在打开「${skill.title}」，下一步选择文档。`);
      quickActionLaunchIdRef.current = window.setTimeout(() => {
        onQuickSkill(skill);
        quickActionLaunchIdRef.current = undefined;
      }, 650);
    } else {
      setQuickSkillNotice("正在进行只读电脑检查，不会改动系统设置。");
      quickActionLaunchIdRef.current = window.setTimeout(() => {
        onQuickSkill(skill);
        quickActionLaunchIdRef.current = undefined;
      }, 650);
    }
    quickNoticeClearIdRef.current = window.setTimeout(() => {
      setQuickSkillNotice("");
      quickNoticeClearIdRef.current = undefined;
    }, 3200);
  };

  const statusChips = buildHomeStatusChips(readinessItems, trustItems, pendingApprovalCount, safetyAlert);
  const draftReady = draft.trim().length > 0;
  const canSubmit = draftReady && !isSubmitting;
  const commandNote = commandFooterNote({
    draftReady,
    isSubmitting,
    connectionState,
    quickSkillNotice,
    submitError
  });
  const commandNoteTone = submitError || connectionState === "offline" ? "warning" : quickSkillNotice || isSubmitting ? "ready" : "";
  const activeAgent = agents.find((agent) => agent.id === workingAgentId) ?? agents[0];
  const activeHelper = getFriendlyAgentCopy(activeAgent);
  const commandPreviewSteps = useMemo(
    () => buildCommandPreviewSteps(draft, quickSkillNotice, safetyAlert, quickSkillIntent),
    [draft, quickSkillIntent, quickSkillNotice, safetyAlert]
  );
  const selectedQuickSkill = quickSkills.find((skill) => skill.id === quickSkillIntent) ?? null;
  const {
    currentTasks,
    displayedTasks,
    activeTaskLabel,
    recentTaskLabel,
    blockedTaskCount,
    runningTaskCount,
    taskPilot,
    taskWorkspaceItems,
    outcomeCards
  } = useMemo(
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

  return (
    <div className="office-workspace" aria-label="Lengrvis 办公室">
      <div className="office-stage">
        <div className="office-headline">
          <div className="office-headline__title">
            <span className="office-headline__eyebrow">
              <Sparkles size={13} aria-hidden="true" />
              本机优先的个人 AI 工作台
            </span>
            <h1>问问 Lengrvis</h1>
            <p>一句话处理文件、文档、应用和这台电脑上的事务。</p>
          </div>
          <div className="office-headline__status" aria-label="当前工作状态">
            <span className={runningTaskCount > 0 ? "home-status-pill home-status-pill--live" : "home-status-pill"}>
              <Radio size={13} aria-hidden="true" />
              {runningTaskCount > 0 ? `${runningTaskCount} 项处理中` : "当前空闲"}
            </span>
            <span className="home-status-pill home-status-pill--private">
              <LockKeyhole size={13} aria-hidden="true" />
              重要修改先确认
            </span>
            <span className="office-headline__legend">
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: activeAgent.accent
                }}
              />
              <strong>{activeHelper.name}</strong>
            </span>
          </div>
        </div>

        <div className="office-map" ref={officeMapRef} style={officeMapStyle}>
          <OfficeLayout workingAgentIds={workingAgentIds} />

          <span className="office-zone-label office-zone-label--pantry">茶水区</span>
          <span className="office-zone-label office-zone-label--gym">专注区</span>
          <span className="office-zone-label office-zone-label--lounge">休息区</span>
          <span className="office-zone-label office-zone-label--restroom">隐私区</span>
          <span className="office-zone-label office-zone-label--workstations">工位区</span>
          <span className="office-zone-label office-zone-label--meeting">计划板</span>
          <div className={`office-patrol-scan ${safetyAlert ? "office-patrol-scan--active" : ""}`} />

          <div className={`office-agents ${isOfficeMapReady ? "office-agents--ready" : ""}`}>
            {isOfficeMapReady
              ? agents.map((agent) => (
                  <OfficeAgent
                    key={agent.id}
                    agent={agent}
                    state={resolveOfficeAgentRuntime(agent, agentState[agent.id], agentState, workingAgentIds)}
                    mapSize={officeMapSize}
                    agentScale={agentVisualScale}
                    isWorking={workingAgentIds.has(agent.id)}
                    isMoving={movingAgents.has(agent.id)}
                    onSelect={() => activateAgent(agent)}
                  />
                ))
              : null}
          </div>
        </div>

        <div className="office-command-dock">
          <div className="office-command-dock__hint">
            <span>说出目标，Lengrvis 会先判断范围和风险</span>
            <span>·</span>
            <span><CornerDownLeft size={11} aria-hidden="true" /> Ctrl + Enter 发送</span>
          </div>
          <textarea
            ref={commandInputRef}
            value={draft}
            disabled={isSubmitting}
            onChange={(event) => {
              setQuickSkillIntent(null);
              onDraftChange(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                if (canSubmit) onSubmitPrompt();
              }
            }}
            placeholder="例如：帮我找一个文件、总结一份文档，或检查这台电脑。"
            aria-invalid={Boolean(submitError)}
            aria-describedby="office-command-status"
          />
          <div className={draftReady ? "command-preview command-preview--ready" : "command-preview"} aria-label="执行预览">
            {commandPreviewSteps.map((step) => {
              const Icon = step.icon;
              return (
                <span key={step.id} className={`command-preview__step command-preview__step--${step.state}`}>
                  <Icon size={13} aria-hidden="true" />
                  <span>
                    <strong>{step.label}</strong>
                    <em>{step.detail}</em>
                  </span>
                </span>
              );
            })}
          </div>
          <div className="command-footer">
            <span
              id="office-command-status"
              className={`command-footer__note ${commandNoteTone ? `command-footer__note--${commandNoteTone}` : ""}`}
              role={submitError ? "alert" : "status"}
            >
              {commandNote}
            </span>
            <button
              className={sendHintPulse ? "button button--primary command-footer__send command-footer__send--hint" : "button button--primary command-footer__send"}
              onClick={() => {
                if (canSubmit) onSubmitPrompt();
              }}
              type="button"
              disabled={!canSubmit}
              aria-busy={isSubmitting}
            >
              {isSubmitting ? <Radio size={16} aria-hidden="true" /> : <CornerDownLeft size={16} aria-hidden="true" />}
              {isSubmitting ? "启动中" : "发送"}
            </button>
          </div>
        </div>
      </div>

      <aside className="office-inspector office-inspector--simple" aria-label="首页建议和最近任务">
        <div className="inspector-card home-examples-card">
          <div className="inspector-card__head">
            <strong>可以这样开始</strong>
            <span>普通话输入即可</span>
          </div>
          <div className="office-quick-actions office-quick-actions--workbench">
            {quickSkills.map((skill) => (
              <button
                key={skill.title}
                type="button"
                className={selectedQuickSkill?.id === skill.id ? "office-quick-card office-quick-card--active" : "office-quick-card"}
                data-testid={`office-template-${skill.id}`}
                data-template-id={skill.id}
                onClick={() => handleQuickSkillClick(skill)}
                aria-pressed={selectedQuickSkill?.id === skill.id}
              >
                <skill.icon className="office-quick-card__icon" size={15} aria-hidden="true" />
                <span className="office-quick-card__body">
                  <span className="office-quick-card__title">
                    <strong>{skill.title}</strong>
                    <em>{skill.summary || quickSkillHint(skill)}</em>
                  </span>
                  <span className="office-quick-card__wizard-line">
                    <b>输入</b>{skill.wizard.input}
                  </span>
                  <span className="office-quick-card__wizard-line">
                    <b>预检</b>{skill.wizard.preflight}
                  </span>
                  <span className="office-quick-card__wizard-line">
                    <b>产出</b>{skill.wizard.output}
                  </span>
                  <small className="office-quick-card__trust">
                    <b>{skill.trust.local}</b>
                    <b>{skill.trust.cloud}</b>
                    <b>{skill.trust.approval}</b>
                    <b>{skill.trust.rollback}</b>
                    <b>{skill.trust.estimate}</b>
                  </small>
                </span>
              </button>
            ))}
          </div>
          {selectedQuickSkill ? (
            <div className="home-skill-wizard" data-testid="office-template-wizard" data-template-id={selectedQuickSkill.id} aria-live="polite">
              <div className="home-skill-wizard__head">
                <span>任务向导</span>
                <strong>{selectedQuickSkill.title}</strong>
              </div>
              <div className="home-skill-wizard__grid">
                <span>
                  <FileText size={13} aria-hidden="true" />
                  <b>输入要求</b>
                  <em>{selectedQuickSkill.wizard.input}</em>
                </span>
                <span>
                  <ShieldCheck size={13} aria-hidden="true" />
                  <b>预检</b>
                  <em>{selectedQuickSkill.wizard.preflight}</em>
                </span>
                <span>
                  <CheckCircle2 size={13} aria-hidden="true" />
                  <b>成果</b>
                  <em>{selectedQuickSkill.wizard.output}</em>
                </span>
              </div>
              <div className="home-skill-wizard__next" data-testid="office-template-next-step">
                <span>{selectedQuickSkill.kind === "prompt" ? "已填入输入框" : selectedQuickSkill.kind === "view" ? "正在打开工具区" : "只读检查启动中"}</span>
                <strong>{selectedQuickSkill.wizard.nextStep}</strong>
              </div>
            </div>
          ) : null}
        </div>

        <div className="inspector-card home-status-strip" data-testid="home-status-strip">
          <div className="inspector-card__head">
            <strong>当前状态</strong>
            <span>{readinessSummary(readinessItems)}</span>
          </div>
          <div className="home-status-grid">
            {statusChips.map((chip) => {
              const Icon = chip.icon;
              return (
                <div key={chip.id} className={`home-status-chip home-status-chip--${chip.tone}`}>
                  <span className="home-status-chip__icon" aria-hidden="true">
                    <Icon size={14} />
                  </span>
                  <div className="home-status-chip__body">
                    <span>{chip.label}</span>
                    <strong>{chip.value}</strong>
                    <em>{chip.detail}</em>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="inspector-card task-list-card">
          <div className="inspector-card__head">
            <strong>最近任务</strong>
          </div>
          <div className="metric-row">
            <div>
              <strong>{currentTasks.length > 0 ? "处理中" : "空闲"}</strong>
              <span>{activeTaskLabel}</span>
            </div>
            <div>
              <strong>最近</strong>
              <span>{recentTaskLabel}</span>
            </div>
          </div>

          <div className="task-list-card__list" style={{ marginTop: 14 }}>
            {displayedTasks.length ? (
              displayedTasks.map((task) => (
                <button
                  key={task.id}
                  type="button"
                  className="task-row"
                  onClick={() => onTaskPilotAction?.(task, task.state === "blocked" ? "approve" : "open")}
                  aria-label={`${taskDisplayTitle(task, "最近任务")}，${taskDisplayState(task)}，${task.state === "blocked" ? "去确认" : "查看进度"}`}
                >
                  <span className={"task-row__dot task-row__dot--" + task.state}>
                    {task.state === "completed" ? (
                      <CheckCircle2 size={14} aria-hidden="true" />
                    ) : task.state === "blocked" ? (
                      <ShieldCheck size={14} aria-hidden="true" />
                    ) : task.state === "failed" ? (
                      <ShieldCheck size={14} aria-hidden="true" />
                    ) : (
                      <Clock size={14} aria-hidden="true" />
                    )}
                  </span>
                  <div className="task-row__body">
                    <strong>{taskDisplayTitle(task, "最近任务")}</strong>
                    <em>{taskDisplayState(task)} · {task.state === "blocked" ? "点此确认" : "点此查看"}</em>
                  </div>
                  <time className="task-row__time">
                    {new Date(task.updatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </time>
                </button>
              ))
            ) : (
              <p className="empty-note">你开始一个需求后，最近进展会显示在这里。</p>
            )}
            {blockedTaskCount > 0 ? (
              <span className="task-list-card__approval">有项目需要你确认</span>
            ) : null}
          </div>
        </div>

        <details className="inspector-card home-more" data-testid="home-more">
          <summary className="home-more__summary">
            <span>更多状态与详情</span>
            <em>隐私与权限、开箱检查、任务驾驶舱、工作区与成果</em>
          </summary>
          <div className="home-more__cards">
            <div className="inspector-card home-trust-card">
              <div className="inspector-card__head">
                <strong>隐私与权限</strong>
                <span>当前策略</span>
              </div>
              <div className="home-trust-grid">
                {trustItems.map((item) => (
                  <div key={item.id} className={`home-trust-item home-trust-item--${item.state}`}>
                    <span className="home-trust-item__icon" aria-hidden="true">
                      {trustIcon(item)}
                    </span>
                    <div>
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                      <em>{item.detail}</em>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="inspector-card home-readiness-card">
              <div className="inspector-card__head">
                <strong>开箱检查</strong>
                <span>{readinessSummary(readinessItems)}</span>
              </div>
              <div className="home-readiness-list">
                {readinessItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`home-readiness-item home-readiness-item--${item.state}`}
                    onClick={() => onReadinessAction(item)}
                  >
                    <span className="home-readiness-item__icon" aria-hidden="true">
                      {readinessIcon(item)}
                    </span>
                    <span className="home-readiness-item__body">
                      <strong>{item.label}</strong>
                      <em>{item.detail}</em>
                    </span>
                    <span className="home-readiness-item__action">{item.actionLabel}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className={`inspector-card task-pilot-card task-pilot-card--${taskPilot.tone}`}>
              <div className="inspector-card__head">
                <strong>任务驾驶舱</strong>
                <span>{taskPilot.status}</span>
              </div>
              <div className="task-pilot-summary">
                <strong>{taskPilot.title}</strong>
                <p>{taskPilot.detail}</p>
              </div>
              <button
                className="task-pilot-action"
                type="button"
                onClick={() => onTaskPilotAction?.(taskPilot.task, taskPilot.action)}
              >
                {taskPilot.action === "approve" ? <ShieldCheck size={14} aria-hidden="true" /> : taskPilot.action === "open" ? <Radio size={14} aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}
                {taskPilot.actionLabel}
              </button>
              <div className="task-pilot-steps" aria-label="任务执行阶段">
                {taskPilot.steps.map((step) => {
                  const Icon = step.icon;
                  return (
                    <div key={step.id} className={`task-pilot-step task-pilot-step--${step.state}`}>
                      <span className="task-pilot-step__icon">
                        <Icon size={13} aria-hidden="true" />
                      </span>
                      <span className="task-pilot-step__copy">
                        <strong>{step.label}</strong>
                        <em>{step.detail}</em>
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="inspector-card task-workspace-card" data-testid="task-workspace-card">
              <div className="inspector-card__head">
                <strong>任务工作区</strong>
                <span>权限与接管</span>
              </div>
              <div className="task-workspace-grid" aria-label="任务工作空间">
                {taskWorkspaceItems.map((item) => (
                  <div key={item.label} className={`task-workspace-item task-workspace-item--${item.tone}`} data-workspace-label={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    <em>{item.detail}</em>
                  </div>
                ))}
              </div>
            </div>

            <div className="inspector-card home-outcomes-card" data-testid="home-outcomes-card">
              <div className="inspector-card__head">
                <strong>成果区</strong>
                <span>最近任务产物</span>
              </div>
              <div className="home-outcome-list" aria-label="任务成果区">
                {outcomeCards.map((card) => (
                  <article key={card.id} className={`home-outcome-card home-outcome-card--${card.tone}`} data-testid={`home-outcome-${card.id}`}>
                    <span className="home-outcome-card__meta">
                      <span>{card.eyebrow}</span>
                      <small>{card.statusLabel}</small>
                    </span>
                    <strong>{card.title}</strong>
                    <p>{card.detail}</p>
                    <em>{card.action}</em>
                  </article>
                ))}
              </div>
            </div>
          </div>
        </details>
      </aside>
    </div>
  );
}

function OfficeAgent({
  agent,
  state,
  mapSize,
  agentScale,
  isWorking,
  isMoving,
  onSelect
}: {
  agent: OfficeAgentDefinition;
  state?: OfficeAgentRuntime;
  mapSize: OfficeMapSize;
  agentScale: number;
  isWorking: boolean;
  isMoving: boolean;
  onSelect: () => void;
}) {
  const runtime = state ?? {
    x: agent.x,
    y: agent.y,
    activity: agent.activities[0],
    pose: "working" as OfficeAgentPose
  };
  const targetPose: OfficeAgentPose = isWorking ? "working" : runtime.pose;
  const pose: OfficeAgentPose = isMoving ? "wander" : targetPose;
  const helper = getFriendlyAgentCopy(agent);
  const activity = isMoving ? "移动中" : isWorking ? "正在协作" : "待命";
  const isLead = agent.scale === "lead" && isWorking;
  const screenPosition = projectOfficePoint(runtime.x, runtime.y, mapSize);
  const visualMetrics = getXiaomaVisualMetrics(pose, isLead, agentScale);
  const hitWidth = roundMetric(Math.max(52, visualMetrics.width * (agent.id === "safety" ? 0.56 : 0.7)));
  const hitHeight = roundMetric(Math.max(48, visualMetrics.height * (agent.id === "safety" ? 0.6 : 0.72)));
  const style = {
    ...agentAnchorStyle,
    left: `${screenPosition.x}px`,
    top: `${screenPosition.y}px`,
    "--agent-accent": agent.accent,
    "--agent-glow": agent.glow,
    "--agent-delay": `${agent.delay}s`,
    "--agent-duration": `${agent.duration}s`,
    "--agent-anchor-x": `${screenPosition.x}px`,
    "--agent-anchor-y": `${screenPosition.y}px`,
    "--agent-map-x": `${runtime.x}px`,
    "--agent-map-y": `${runtime.y}px`,
    "--agent-z-index": Math.round(runtime.y + (isWorking ? 8 : 0)),
    "--agent-scale": agentScale,
    "--agent-width": `${hitWidth}px`,
    "--agent-height": `${hitHeight}px`,
    "--agent-visual-width": `${visualMetrics.width}px`,
    "--agent-visual-height": `${visualMetrics.height}px`,
    "--agent-pose-offset-x": `${visualMetrics.offsetX}px`,
    "--agent-pose-offset-y": `${visualMetrics.offsetY}px`,
    "--agent-bubble-y": `${visualMetrics.bubbleY}px`,
    "--agent-label-y": `${visualMetrics.labelY}px`,
    "--agent-halo-left": `${visualMetrics.haloLeft}px`,
    "--agent-halo-top": `${visualMetrics.haloTop}px`,
    "--agent-halo-width": `${visualMetrics.haloWidth}px`,
    "--agent-halo-height": `${visualMetrics.haloHeight}px`
  } as CSSProperties;
  const className = cx(
    "office-agent",
    `office-agent--${pose}`,
    `office-agent--pose-${pose}`,
    `office-agent--runtime-${runtime.pose}`,
    `office-agent--target-${targetPose}`,
    `office-agent--agent-${agent.id}`,
    isWorking ? "office-agent--active" : "office-agent--idle",
    isMoving ? "office-agent--moving" : "office-agent--still",
    isLead ? "office-agent--lead" : "office-agent--standard"
  );

  return (
    <button
      type="button"
      className={className}
      style={style}
      onClick={onSelect}
      aria-label={`${helper.name}, ${helper.role}, ${activity}`}
      data-agent-id={agent.id}
      data-pose={pose}
      data-runtime-pose={runtime.pose}
      data-target-pose={targetPose}
      data-anchor-x={runtime.x}
      data-anchor-y={runtime.y}
    >
      <span className="office-agent__ground-anchor" aria-hidden="true" style={agentGroundAnchorStyle} />
      <span className="office-agent__halo" aria-hidden="true" style={agentHaloStyle} />
      <span className="office-agent__bubble" style={agentBubbleStyle}>{activity}</span>
      <span className="office-agent__visual" aria-hidden="true" style={agentSpriteStyle}>
        <PonyAgent accent={agent.accent} pose={pose} isLead={isLead} isWorking={isWorking} isMoving={isMoving} />
      </span>
      <span className="office-agent__label" style={agentLabelStyle}>
        <strong>{helper.name}</strong>
        <span>{helper.role}</span>
      </span>
    </button>
  );
}

function resolveOfficeAgentRuntime(
  agent: OfficeAgentDefinition,
  state: OfficeAgentRuntime | undefined,
  allAgentState: Record<string, OfficeAgentRuntime>,
  workingAgentIds: ReadonlySet<string>
): OfficeAgentRuntime | undefined {
  if (agent.id !== "safety" || !state || !workingAgentIds.has("pm") || !workingAgentIds.has("safety")) {
    return state;
  }

  const lengrvisState = allAgentState.pm;
  if (!lengrvisState || !isSameOfficePoint(state, lengrvisState)) {
    return state;
  }

  return {
    ...state,
    x: safetySharedAnchorFallback.x,
    y: safetySharedAnchorFallback.y
  };
}

function isSameOfficePoint(a: Pick<OfficeAgentRuntime, "x" | "y">, b: Pick<OfficeAgentRuntime, "x" | "y">) {
  return Math.abs(a.x - b.x) <= sharedAnchorTolerance && Math.abs(a.y - b.y) <= sharedAnchorTolerance;
}

function getMovingAgentIds(
  agents: OfficeAgentDefinition[],
  current: Record<string, OfficeAgentRuntime>,
  next: Record<string, OfficeAgentRuntime>
) {
  const moving = new Set<string>();
  for (const agent of agents) {
    const prev = current[agent.id];
    const incoming = next[agent.id];
    if (!prev || !incoming) continue;
    const dx = Math.abs(prev.x - incoming.x);
    const dy = Math.abs(prev.y - incoming.y);
    if (dx > 1.5 || dy > 1.5) moving.add(agent.id);
  }
  return moving;
}

function getXiaomaVisualMetrics(pose: OfficeAgentPose, isLead: boolean, agentScale = 1): OfficeAgentVisualMetrics {
  const base = xiaomaBaseVisualMetrics[pose];
  const scale = (isLead ? 168 / 140 : 1) * agentScale;
  const width = roundMetric(base.width * scale);
  const height = roundMetric(base.height * scale);
  const offsetX = roundMetric(base.offsetX * scale);
  const offsetY = roundMetric(base.offsetY * scale);
  const haloWidth = roundMetric(base.haloWidth * scale);
  const haloHeight = roundMetric(base.haloHeight * scale);

  return {
    width,
    height,
    offsetX,
    offsetY,
    bubbleY: roundMetric(base.bubbleY * scale),
    labelY: roundMetric(base.labelY * scale),
    haloLeft: roundMetric(offsetX - haloWidth / 2),
    haloTop: roundMetric(offsetY - height - 6 * scale),
    haloWidth,
    haloHeight
  };
}

function PonyAgent({ accent, pose, isLead, isWorking, isMoving }: PonyAgentProps) {
  const gif = isMoving ? xiaomaWalkingGif : xiaomaPoseGifs[pose] ?? xiaomaStandbyGif;
  const className = cx(
    "pony-agent-svg",
    `pony-agent-svg--${pose}`,
    `pony-agent-svg--pose-${pose}`,
    isLead ? "pony-agent-svg--lead" : "pony-agent-svg--standard",
    isWorking ? "pony-agent-svg--working" : "pony-agent-svg--idle",
    isMoving ? "pony-agent-svg--moving" : "pony-agent-svg--still"
  );

  return (
    <span
      className={className}
      aria-hidden="true"
      style={{ "--agent-accent": accent, width: "100%", height: "100%" } as CSSProperties}
    >
      <img className="xiaoma-agent-gif" src={gif} alt="" draggable={false} />
    </span>
  );
}

function roundMetric(value: number) {
  return Math.round(value * 10) / 10;
}

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

function OfficeLayout({ workingAgentIds }: { workingAgentIds: ReadonlySet<string> }) {
  return (
    <div className="office-layout" aria-hidden="true">
      <div className="office-grid" />
      <img className="office-furniture office-furniture--water" src={officeWaterBar} alt="" draggable={false} />
      <img className="office-furniture office-furniture--treadmill" src={officeTreadmill} alt="" draggable={false} />
      <img className="office-furniture office-furniture--toilet" src={officeToilet} alt="" draggable={false} />
      {officeSlots.map((slot) => (
        <Workstation key={slot.agentId} slot={slot} active={workingAgentIds.has(slot.agentId)} />
      ))}
    </div>
  );
}

function Workstation({ slot, active }: { slot: OfficeSlot; active: boolean }) {
  const screenImage = active
    ? xiaomaScreenWorkingGifs[slot.agentId] ?? officeScreenOn
    : slot.boss
      ? officeScreenOn
      : officeScreenIdle;
  const style = {
    "--slot-x": `${slot.x}px`,
    "--slot-y": `${slot.y}px`
  } as CSSProperties;

  return (
    <div className={slot.boss ? "office-workstation office-workstation--boss" : "office-workstation"} style={style}>
      <img className="office-workstation__shadow" src={slot.boss ? officeShadowBoss : officeShadow} alt="" draggable={false} />
      <img className="office-workstation__desk" src={slot.boss ? officeDeskBoss : officeDesk} alt="" draggable={false} />
      <img className="office-workstation__screen" src={screenImage} alt="" draggable={false} />
      <img className="office-workstation__chair" src={slot.boss ? officeChairBoss : officeChair} alt="" draggable={false} />
    </div>
  );
}

function getFriendlyAgentCopy(agent: OfficeAgentDefinition | undefined) {
  switch (agent?.id) {
    case "file":
      return { name: "文件", role: "查找文档" };
    case "computer":
      return { name: "电脑", role: "检查设备" };
    case "app":
      return { name: "应用", role: "应用协作" };
    case "browser":
      return { name: "网页", role: "查询资料" };
    case "search":
      return { name: "搜索", role: "寻找答案" };
    case "safety":
      return { name: "审批", role: "修改前确认" };
    case "pm":
    default:
      return { name: "Lengrvis", role: "随时待命" };
  }
}

function quickSkillHint(skill: OfficeQuickSkill): string {
  if (skill.kind === "action" && skill.action === "system-check") return "立即只读检查";
  if (skill.kind === "view" && skill.id === "summarize-document") return "打开文档操作区";
  return skill.kind === "prompt" ? "填好后点发送" : "打开页面";
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
      {
        id: "understand",
        label: "理解目标",
        detail: "查找可清理的大文件",
        state: "ready",
        icon: Sparkles
      },
      {
        id: "guard",
        label: "确认范围",
        detail: safetyAlert ? "需要你批准" : "发送后先选文件夹",
        state: safetyAlert ? "blocked" : "active",
        icon: safetyAlert ? ShieldCheck : LockKeyhole
      },
      {
        id: "execute",
        label: "执行反馈",
        detail: "清理前会确认",
        state: hasDraft ? "active" : "idle",
        icon: Radio
      }
    ];
  }

  if (intent === "summarize-document") {
    return [
      {
        id: "understand",
        label: "打开文档区",
        detail: "直接进入文档操作",
        state: "ready",
        icon: Sparkles
      },
      {
        id: "guard",
        label: "选择文档",
        detail: "只读取你选的文件",
        state: "active",
        icon: LockKeyhole
      },
      {
        id: "execute",
        label: "读取/总结/提问",
        detail: "按钮按步骤可用",
        state: "active",
        icon: Radio
      }
    ];
  }

  if (intent === "check-computer") {
    return [
      {
        id: "understand",
        label: "只读读取",
        detail: "读取系统快照",
        state: "ready",
        icon: Sparkles
      },
      {
        id: "guard",
        label: "不改设置",
        detail: "不会动系统配置",
        state: "ready",
        icon: LockKeyhole
      },
      {
        id: "execute",
        label: "电脑健康",
        detail: "暂未读取不等于故障",
        state: "active",
        icon: Radio
      }
    ];
  }

  return [
    {
      id: "understand",
      label: "理解目标",
      detail: hasDraft || hasQuickNotice ? "已准备拆解" : "等待输入",
      state: hasDraft || hasQuickNotice ? "ready" : "idle",
      icon: Sparkles
    },
    {
      id: "guard",
      label: "确认范围",
      detail: safetyAlert ? "需要你批准" : hasDraft ? "先看权限" : "不改系统",
      state: safetyAlert ? "blocked" : hasDraft ? "active" : "idle",
      icon: safetyAlert ? ShieldCheck : LockKeyhole
    },
    {
      id: "execute",
      label: "执行反馈",
      detail: hasDraft ? "实时显示进度" : "任务会留痕",
      state: hasDraft ? "active" : "idle",
      icon: hasDraft ? Radio : Clock
    }
  ];
}

function readinessSummary(items: HomeReadinessItem[]) {
  const readyCount = items.filter((item) => item.state === "ready").length;
  return `${readyCount}/${items.length} 已就绪`;
}

function readinessIcon(item: HomeReadinessItem) {
  if (item.id === "scope") return <FolderOpen size={14} />;
  if (item.id === "privacy") return <LockKeyhole size={14} />;
  if (item.id === "connection") return <Radio size={14} />;
  if (item.state === "ready") return <CheckCircle2 size={14} />;
  return <Sparkles size={14} />;
}

function trustIcon(item: HomeTrustItem) {
  if (item.id === "files") return <FolderOpen size={14} />;
  if (item.id === "approval") return <ShieldCheck size={14} />;
  if (item.id === "upload") return <LockKeyhole size={14} />;
  if (item.state === "ready") return <CheckCircle2 size={14} />;
  return <Radio size={14} />;
}

interface HomeStatusChip {
  id: "connection" | "ai" | "files" | "approval";
  label: string;
  value: string;
  detail: string;
  tone: "ready" | "warning" | "blocked";
  icon: LucideIcon;
}

function buildHomeStatusChips(
  readinessItems: HomeReadinessItem[],
  trustItems: HomeTrustItem[],
  pendingApprovalCount: number,
  safetyAlert: boolean
): HomeStatusChip[] {
  const connection = readinessItems.find((item) => item.id === "connection");
  const aiTrust = trustItems.find((item) => item.id === "ai");
  const fileTrust = trustItems.find((item) => item.id === "files");
  const hasApprovalWork = pendingApprovalCount > 0 || safetyAlert;

  return [
    {
      id: "connection",
      label: "连接",
      value: connection?.state === "ready" ? "已连接" : connection?.state === "warning" ? "连接中" : "待恢复",
      detail: connection?.detail ?? "服务状态会显示在这里",
      tone: connection?.state === "ready" ? "ready" : connection?.state === "warning" ? "warning" : "blocked",
      icon: Radio
    },
    {
      id: "ai",
      label: "AI 运行",
      value: aiTrust?.value ?? "高效模式",
      detail: aiTrust?.detail ?? "按当前模式执行",
      tone: aiTrust?.state === "warning" ? "warning" : "ready",
      icon: LockKeyhole
    },
    {
      id: "files",
      label: "文件范围",
      value: fileTrust?.value ?? "未授权",
      detail: fileTrust?.detail ?? "先选择桌面、下载或文件夹",
      tone: fileTrust?.state === "ready" ? "ready" : "warning",
      icon: FolderOpen
    },
    {
      id: "approval",
      label: "审批",
      value: pendingApprovalCount > 0 ? `${pendingApprovalCount} 项待确认` : safetyAlert ? "需复核" : "无待办",
      detail: hasApprovalWork ? "高风险动作会停下等待你确认" : "删除、移动、写入前会先确认",
      tone: hasApprovalWork ? "blocked" : "ready",
      icon: ShieldCheck
    }
  ];
}

function commandFooterNote({
  draftReady,
  isSubmitting,
  connectionState,
  quickSkillNotice,
  submitError
}: {
  draftReady: boolean;
  isSubmitting: boolean;
  connectionState: ConnectionState;
  quickSkillNotice: string;
  submitError: string | null;
}): string {
  if (submitError) return submitError;
  if (connectionState === "offline") return "连接状态仍在恢复；发送后会尝试启动任务，失败时会保留后端返回的原因。";
  if (isSubmitting) return "正在启动任务，返回结果前不会重复创建。";
  if (quickSkillNotice) return quickSkillNotice;
  if (draftReady) return "准备好了，发送后会进入任务流。";
  return "涉及重要修改前，Lengrvis 会先征得你的确认。";
}
