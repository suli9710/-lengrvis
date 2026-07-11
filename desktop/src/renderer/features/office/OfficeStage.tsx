import type { CSSProperties } from "react";

import xiaomaScreenAppGif from "../../assets/xiaoma-agent/fc_screen_working_apk_use.gif";
import xiaomaScreenFileGif from "../../assets/xiaoma-agent/fc_screen_working_file_use.gif";
import xiaomaScreenMainGif from "../../assets/xiaoma-agent/fc_screen_working_main.gif";
import xiaomaScreenSearchGif from "../../assets/xiaoma-agent/fc_screen_working_search_or_browser_use.gif";
import xiaomaScreenComputerGif from "../../assets/xiaoma-agent/fc_screen_working_win_use.gif";
import xiaomaScreenAppStill from "../../assets/xiaoma-agent/fc_screen_working_apk_use_still.png?no-inline";
import xiaomaScreenFileStill from "../../assets/xiaoma-agent/fc_screen_working_file_use_still.png?no-inline";
import xiaomaScreenMainStill from "../../assets/xiaoma-agent/fc_screen_working_main_still.png?no-inline";
import xiaomaScreenSearchStill from "../../assets/xiaoma-agent/fc_screen_working_search_or_browser_use_still.png?no-inline";
import xiaomaScreenComputerStill from "../../assets/xiaoma-agent/fc_screen_working_win_use_still.png?no-inline";
import officeChair from "../../assets/office-analysis/workstation-parts/chair.png";
import officeChairBoss from "../../assets/office-analysis/workstation-parts/chair_boss.png";
import officeDesk from "../../assets/office-analysis/workstation-parts/desk.png";
import officeDeskBoss from "../../assets/office-analysis/workstation-parts/desk_boss.png";
import officeScreenIdle from "../../assets/office-analysis/workstation-parts/screen_img.png?no-inline";
import officeScreenOn from "../../assets/office-analysis/workstation-parts/screen_on.png?no-inline";
import officeShadow from "../../assets/office-analysis/workstation-parts/shadow.png";
import officeShadowBoss from "../../assets/office-analysis/workstation-parts/shadow_boss.png";
import officeToilet from "../../assets/office-analysis/workstation-parts/toilet.png";
import officeTreadmill from "../../assets/office-analysis/workstation-parts/treadmill.png";
import officeWaterBar from "../../assets/office-analysis/workstation-parts/water_bar.png";
import { useUiPreferences } from "../../lib/uiPreferences";
import {
  projectOfficePoint,
  type OfficeAgentDefinition,
  type OfficeAgentPose,
  type OfficeAgentRuntime,
  type OfficeMapSize
} from "./model";
import { PonyRig, type PonyFeedback } from "./PonyRig";
import { ponyClipForOfficePose } from "./ponyMotion";
import { useOfficeTravelController } from "./useOfficeTravelController";

interface OfficeSlot {
  agentId: string;
  x: number;
  y: number;
  boss?: boolean;
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


const xiaomaScreenWorkingGifs: Record<string, string> = {
  pm: xiaomaScreenMainGif,
  app: xiaomaScreenAppGif,
  file: xiaomaScreenFileGif,
  computer: xiaomaScreenComputerGif,
  browser: xiaomaScreenSearchGif,
  search: xiaomaScreenSearchGif
};

const xiaomaScreenWorkingStills: Record<string, string> = {
  pm: xiaomaScreenMainStill,
  app: xiaomaScreenAppStill,
  file: xiaomaScreenFileStill,
  computer: xiaomaScreenComputerStill,
  browser: xiaomaScreenSearchStill,
  search: xiaomaScreenSearchStill
};

const officeSlots: OfficeSlot[] = [
  { agentId: "pm", x: 608, y: 160, boss: true },
  { agentId: "app", x: 864, y: 160 },
  { agentId: "computer", x: 608, y: 416 },
  { agentId: "browser", x: 864, y: 416 },
  { agentId: "file", x: 608, y: 672 },
  { agentId: "search", x: 864, y: 672 }
];

const xiaomaBaseVisualMetrics: Omit<OfficeAgentVisualMetrics, "haloLeft" | "haloTop"> = {
  width: 150,
  height: 124,
  offsetX: 0,
  offsetY: 0,
  bubbleY: -150,
  labelY: 8,
  haloWidth: 168,
  haloHeight: 136
};

const safetySharedAnchorFallback = { x: 1030, y: 382 };
const sharedAnchorTolerance = 1.5;

const agentAnchorStyle = {
  position: "absolute",
  left: 0,
  top: 0,
  overflow: "visible",
  pointerEvents: "auto"
} as CSSProperties;

const agentGroundAnchorStyle = {
  position: "absolute",
  left: "50%",
  top: "100%",
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
  isPrimary,
  motionVisible,
  feedback,
  onSelect
}: {
  agent: OfficeAgentDefinition;
  state?: OfficeAgentRuntime;
  mapSize: OfficeMapSize;
  agentScale: number;
  isWorking: boolean;
  isPrimary: boolean;
  motionVisible: boolean;
  feedback?: PonyFeedback;
  onSelect: () => void;
}) {
  const { effectiveMotion } = useUiPreferences();
  const runtime = state ?? {
    x: agent.x,
    y: agent.y,
    activity: agent.activities[0],
    pose: "working" as OfficeAgentPose
  };
  const targetPose: OfficeAgentPose = isWorking ? "working" : runtime.pose;
  const { anchorRef, phase, facing } = useOfficeTravelController({
    target: { x: runtime.x, y: runtime.y },
    mapSize,
    reducedMotion: effectiveMotion === "reduced",
    paused: effectiveMotion === "full" && !motionVisible
  });
  const isMoving = phase !== "idle";
  const displayPose: OfficeAgentPose = isMoving ? "wander" : targetPose;
  const clip = ponyClipForOfficePose(targetPose, phase);
  const helper = getFriendlyAgentCopy(agent);
  const activity = feedback === "completed"
    ? "已经完成"
    : feedback === "failed"
      ? "需要处理"
      : feedback === "approval"
        ? "等你确认"
        : feedback === "selected"
          ? "我来帮你"
          : phase === "turning"
            ? "正在转身"
            : isMoving
              ? "正在就位"
              : runtime.activity || (isWorking ? "正在处理" : "随时待命");
  const isLead = agent.scale === "lead";
  const screenPosition = projectOfficePoint(runtime.x, runtime.y, mapSize);
  const visualMetrics = getXiaomaVisualMetrics(isLead, agentScale);
  const hitWidth = roundMetric(Math.max(52, visualMetrics.width * (agent.id === "safety" ? 0.56 : 0.7)));
  const hitHeight = roundMetric(Math.max(48, visualMetrics.height * (agent.id === "safety" ? 0.6 : 0.72)));
  const style = {
    ...agentAnchorStyle,
    "--agent-accent": agent.accent,
    "--agent-glow": agent.glow,
    "--agent-anchor-x": `${screenPosition.x}px`,
    "--agent-anchor-y": `${screenPosition.y}px`,
    "--agent-map-x": `${runtime.x}px`,
    "--agent-map-y": `${runtime.y}px`,
    "--agent-z-index": Math.round(runtime.y + (isWorking ? 8 : 0)),
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
  const animateCharacter = effectiveMotion === "full" && motionVisible && (
    isPrimary || isWorking || isMoving || Boolean(feedback)
  );
  const className = cx(
    "office-agent",
    `office-agent--${displayPose}`,
    `office-agent--pose-${displayPose}`,
    `office-agent--runtime-${runtime.pose}`,
    `office-agent--target-${targetPose}`,
    `office-agent--phase-${phase}`,
    `office-agent--facing-${facing}`,
    `office-agent--agent-${agent.id}`,
    isWorking ? "office-agent--active" : "office-agent--idle",
    isMoving ? "office-agent--moving" : "office-agent--still",
    isLead ? "office-agent--lead" : "office-agent--standard",
    animateCharacter ? "office-agent--animated" : "office-agent--static",
    !motionVisible ? "office-agent--motion-paused" : null,
    isPrimary ? "office-agent--primary" : "office-agent--secondary",
    feedback ? `office-agent--feedback-${feedback}` : null
  );

  return (
    <button
      ref={anchorRef}
      type="button"
      className={className}
      style={style}
      onClick={onSelect}
      aria-label={`${helper.name}, ${helper.role}, ${activity}`}
      data-agent-id={agent.id}
      data-pose={displayPose}
      data-runtime-pose={runtime.pose}
      data-target-pose={targetPose}
      data-anchor-x={runtime.x}
      data-anchor-y={runtime.y}
      data-facing={facing}
      data-motion-phase={phase}
      data-clip={clip}
    >
      <span className="office-agent__ground-anchor" aria-hidden="true" style={agentGroundAnchorStyle} />
      <span className="office-agent__halo" aria-hidden="true" style={agentHaloStyle} />
      <span className="office-agent__bubble" style={agentBubbleStyle}>{activity}</span>
      <span className="office-agent__visual" aria-hidden="true" style={agentSpriteStyle}>
        <PonyRig
          accent={agent.accent}
          clip={clip}
          facing={facing}
          motionPhase={phase}
          feedback={feedback}
          isLead={isLead}
          animate={animateCharacter}
          paused={!motionVisible}
        />
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

export function OfficeLayout({
  workingAgentIds,
  motionVisible
}: {
  workingAgentIds: ReadonlySet<string>;
  motionVisible: boolean;
}) {
  const { effectiveMotion } = useUiPreferences();
  const animateScreens = effectiveMotion === "full" && motionVisible;
  return (
    <div className="office-layout" aria-hidden="true">
      <div className="office-grid" />
      <img className="office-furniture office-furniture--water" src={officeWaterBar} alt="" draggable={false} />
      <img className="office-furniture office-furniture--treadmill" src={officeTreadmill} alt="" draggable={false} />
      <img className="office-furniture office-furniture--toilet" src={officeToilet} alt="" draggable={false} />
      {officeSlots.map((slot) => (
        <Workstation
          key={slot.agentId}
          slot={slot}
          active={workingAgentIds.has(slot.agentId)}
          animate={animateScreens}
        />
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

function getXiaomaVisualMetrics(isLead: boolean, agentScale = 1): OfficeAgentVisualMetrics {
  const scale = (isLead ? 168 / 140 : 1) * agentScale;
  const width = roundMetric(xiaomaBaseVisualMetrics.width * scale);
  const height = roundMetric(xiaomaBaseVisualMetrics.height * scale);
  const offsetX = roundMetric(xiaomaBaseVisualMetrics.offsetX * scale);
  const offsetY = roundMetric(xiaomaBaseVisualMetrics.offsetY * scale);
  const haloWidth = roundMetric(xiaomaBaseVisualMetrics.haloWidth * scale);
  const haloHeight = roundMetric(xiaomaBaseVisualMetrics.haloHeight * scale);

  return {
    width,
    height,
    offsetX,
    offsetY,
    bubbleY: roundMetric(xiaomaBaseVisualMetrics.bubbleY * scale),
    labelY: roundMetric(xiaomaBaseVisualMetrics.labelY * scale),
    haloLeft: roundMetric(offsetX - haloWidth / 2),
    haloTop: roundMetric(offsetY - height - 6 * scale),
    haloWidth,
    haloHeight
  };
}

function Workstation({ slot, active, animate }: { slot: OfficeSlot; active: boolean; animate: boolean }) {
  const screen = resolveWorkstationScreen(slot.agentId, Boolean(slot.boss), active, animate);
  const style = {
    "--slot-x": `${slot.x}px`,
    "--slot-y": `${slot.y}px`
  } as CSSProperties;

  return (
    <div className={slot.boss ? "office-workstation office-workstation--boss" : "office-workstation"} style={style}>
      <img className="office-workstation__shadow" src={slot.boss ? officeShadowBoss : officeShadow} alt="" draggable={false} />
      <img className="office-workstation__desk" src={slot.boss ? officeDeskBoss : officeDesk} alt="" draggable={false} />
      <img
        key={screen.src}
        className="office-workstation__screen"
        src={screen.src}
        alt=""
        draggable={false}
        decoding="async"
        onError={(event) => {
          const image = event.currentTarget;
          if (image.dataset.fallbackApplied === "true" || screen.fallbackSrc === screen.src) {
            image.onerror = null;
            return;
          }
          image.dataset.fallbackApplied = "true";
          image.src = screen.fallbackSrc;
        }}
      />
      <img className="office-workstation__chair" src={slot.boss ? officeChairBoss : officeChair} alt="" draggable={false} />
    </div>
  );
}

export function resolveWorkstationScreen(agentId: string, boss: boolean, active: boolean, animate: boolean) {
  const stillSrc = xiaomaScreenWorkingStills[agentId] ?? officeScreenOn;
  if (active) {
    return {
      src: animate ? xiaomaScreenWorkingGifs[agentId] ?? stillSrc : stillSrc,
      fallbackSrc: stillSrc
    };
  }
  const idleSrc = boss ? officeScreenOn : officeScreenIdle;
  return { src: idleSrc, fallbackSrc: officeScreenOn };
}

function roundMetric(value: number) {
  return Math.round(value * 10) / 10;
}

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}
