import type { CSSProperties } from "react";

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
  projectOfficePoint,
  type OfficeAgentDefinition,
  type OfficeAgentPose,
  type OfficeAgentRuntime,
  type OfficeMapSize
} from "./model";

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
export const agentTravelDurationMs = 4200;

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

export function OfficeAgent({
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

export function resolveOfficeAgentRuntime(
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

export function getMovingAgentIds(
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

export function OfficeLayout({ workingAgentIds }: { workingAgentIds: ReadonlySet<string> }) {
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

export function getFriendlyAgentCopy(agent: OfficeAgentDefinition | undefined) {
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

function isSameOfficePoint(a: Pick<OfficeAgentRuntime, "x" | "y">, b: Pick<OfficeAgentRuntime, "x" | "y">) {
  return Math.abs(a.x - b.x) <= sharedAnchorTolerance && Math.abs(a.y - b.y) <= sharedAnchorTolerance;
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

function roundMetric(value: number) {
  return Math.round(value * 10) / 10;
}

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}
