import { CheckCircle2, Clock, CornerDownLeft, ShieldCheck, type LucideIcon } from "lucide-react";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";

import type { TaskEvent } from "../../../shared/types";
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

export interface OfficeQuickSkill {
  icon: LucideIcon;
  title: string;
  prompt: string;
}

export interface OfficeSceneProps {
  agents: OfficeAgentDefinition[];
  draft: string;
  recentTasks: TaskEvent[];
  quickSkills: OfficeQuickSkill[];
  activeAgentId: string;
  onDraftChange: (value: string) => void;
  onSubmitPrompt: () => void;
  onAgentSelect: (prompt: string) => void;
  onQuickSkill: (prompt: string) => void;
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
  activeAgentId,
  onDraftChange,
  onSubmitPrompt,
  onAgentSelect,
  onQuickSkill,
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
  const didSyncWorkingAgentsRef = useRef(false);

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

  const runningTaskCount = recentTasks.filter((task) => task.state === "running" || task.state === "queued").length;
  const blockedTaskCount = recentTasks.filter((task) => task.state === "blocked").length;
  const displayedTasks = recentTasks.slice(0, 3);
  const activeAgent = agents.find((agent) => agent.id === workingAgentId) ?? agents[0];
  const activeHelper = getFriendlyAgentCopy(activeAgent);
  const isOfficeMapReady = officeMapSize.width > 0 && officeMapSize.height > 0;
  const officeScale = isOfficeMapReady
    ? Math.min(officeMapSize.width / officeViewBox.width, officeMapSize.height / officeViewBox.height)
    : 1;
  const agentVisualScale = isOfficeMapReady ? Math.min(1, Math.max(0.56, officeScale / 0.58)) : 1;
  const officeMapStyle = { "--office-scale": officeScale } as CSSProperties;

  return (
    <div className="office-workspace" aria-label="Marvis office">
      <div className="office-stage">
        <div className="office-headline">
          <div className="office-headline__title">
            <h1>Ask Mavris</h1>
            <p>Get help with files, documents, apps, and this computer.</p>
          </div>
          <div className="office-headline__legend">
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: activeAgent.accent
              }}
            />
            Ready to help · <strong>{activeHelper.name}</strong>
          </div>
        </div>

        <div className="office-map" ref={officeMapRef} style={officeMapStyle}>
          <OfficeLayout workingAgentIds={workingAgentIds} />

          <span className="office-zone-label office-zone-label--pantry">Break area</span>
          <span className="office-zone-label office-zone-label--gym">Focus area</span>
          <span className="office-zone-label office-zone-label--lounge">Rest area</span>
          <span className="office-zone-label office-zone-label--restroom">Private area</span>
          <span className="office-zone-label office-zone-label--workstations">Desk area</span>
          <span className="office-zone-label office-zone-label--meeting">Planning board</span>
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
            <span>What would you like help with?</span>
            <span>·</span>
            <span><CornerDownLeft size={11} aria-hidden="true" /> Ctrl + Enter to send</span>
          </div>
          <textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                onSubmitPrompt();
              }
            }}
            placeholder="Ask Mavris to find a file, summarize a document, or check this computer."
          />
          <div className="command-footer">
            <span className="command-footer__note">Mavris will ask before making important changes.</span>
            <button className="button button--primary command-footer__send" onClick={() => onSubmitPrompt()} type="button">
              <CornerDownLeft size={16} aria-hidden="true" />
              Send
            </button>
          </div>
        </div>
      </div>

      <aside className="office-inspector office-inspector--simple" aria-label="Home suggestions and recent work">
        <div className="inspector-card home-examples-card">
          <div className="inspector-card__head">
            <strong>Try one of these</strong>
          </div>
          <div className="office-quick-actions">
            {quickSkills.slice(0, 3).map((skill) => (
              <button key={skill.title} type="button" onClick={() => onQuickSkill(skill.prompt)}>
                <skill.icon size={14} aria-hidden="true" />
                <span>{skill.title}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="inspector-card task-list-card">
          <div className="inspector-card__head">
            <strong>Recent work</strong>
          </div>
          <div className="metric-row">
            <div>
              <strong>{runningTaskCount}</strong>
              <span>In progress</span>
            </div>
            <div>
              <strong>{displayedTasks.length}</strong>
              <span>Shown here</span>
            </div>
          </div>

          <div className="task-list-card__list" style={{ marginTop: 14 }}>
            {displayedTasks.length ? (
              displayedTasks.map((task) => (
                <button key={task.id} type="button" className="task-row">
                  <span className={"task-row__dot task-row__dot--" + task.state}>
                    {task.state === "completed" ? (
                      <CheckCircle2 size={14} aria-hidden="true" />
                    ) : task.state === "blocked" || task.state === "failed" ? (
                      <ShieldCheck size={14} aria-hidden="true" />
                    ) : (
                      <Clock size={14} aria-hidden="true" />
                    )}
                  </span>
                  <div className="task-row__body">
                    <strong>{task.title}</strong>
                    <em>{friendlyTaskState(task.state)}</em>
                  </div>
                  <time className="task-row__time">
                    {new Date(task.updatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </time>
                </button>
              ))
            ) : (
              <p className="empty-note">Your recent work will appear here.</p>
            )}
            {blockedTaskCount > 0 ? (
              <span className="task-list-card__approval">{blockedTaskCount} item needs your review</span>
            ) : null}
          </div>
        </div>
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
  const activity = isMoving ? "Moving" : isWorking ? "Helping now" : "Available";
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

  const marvisState = allAgentState.pm;
  if (!marvisState || !isSameOfficePoint(state, marvisState)) {
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
      return { name: "Files", role: "Finds documents" };
    case "computer":
      return { name: "Computer", role: "Checks this device" };
    case "app":
      return { name: "Apps", role: "Opens tools" };
    case "browser":
      return { name: "Web", role: "Looks things up" };
    case "search":
      return { name: "Search", role: "Finds answers" };
    case "safety":
      return { name: "Review", role: "Asks before changes" };
    case "pm":
    default:
      return { name: "Mavris", role: "Ready to help" };
  }
}

function friendlyTaskState(state: TaskEvent["state"]): string {
  if (state === "completed") return "Done";
  if (state === "running") return "Working";
  if (state === "blocked") return "Needs review";
  if (state === "failed") return "Could not finish";
  return "Waiting";
}
