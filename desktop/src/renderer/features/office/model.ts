import type { LucideIcon } from "lucide-react";
import { AppWindow, FolderOpen, Globe2, Laptop, Search, ShieldCheck, Sparkles } from "lucide-react";

import type { AgentConversation, Plan, SafetyReview, TaskEvent } from "../../../shared/types";
import { zhAgentName } from "../../lib/zh";

export type OfficeAgentPose = "working" | "coffee" | "treadmill" | "restroom" | "nap" | "wander" | "review";

export interface OfficeAgentDefinition {
  id: string;
  name: string;
  role: string;
  icon: LucideIcon;
  prompt: string;
  accent: string;
  glow: string;
  x: number;
  y: number;
  wanderX: number;
  wanderY: number;
  delay: number;
  duration: number;
  scale?: "lead" | "standard";
  activities: string[];
}

export interface OfficeAgentRuntime {
  /** Fixed-canvas foot/ground anchor point, not the visual center of the agent component. */
  x: number;
  y: number;
  activity: string;
  pose: OfficeAgentPose;
}

export interface OfficeMapSize {
  width: number;
  height: number;
}

interface OfficeFootPoint {
  x: number;
  y: number;
}

interface SafeBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

interface FurnitureRect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

const staticFurniture = {
  waterBar: { x: 12, y: 60, width: 398, height: 262 },
  treadmill: { x: 12, y: 296, width: 398, height: 311 },
  toilet: { x: 12, y: 574, width: 398, height: 314 }
} as const;

type StaticFurnitureName = keyof typeof staticFurniture;

interface LeisureSpot extends OfficeFootPoint {
  pose: OfficeAgentPose;
  activity: string;
  bounds?: SafeBounds;
  jitter?: OfficeFootPoint;
  allowFurniture?: StaticFurnitureName;
}

export const officeViewBox = { width: 1088, height: 896 };

const chairYOffset = 65;
const workstationFootYOffset = chairYOffset + 52;
const workstationFrameWidth = 192;
const workstationChairLeft = 40;
const workstationChairWidth = 70;
const workstationBossChairLeft = 30;
const workstationBossChairWidth = 86;
const workstationSlots = {
  pm: { x: 608, y: 160 },
  app: { x: 864, y: 160 },
  computer: { x: 608, y: 416 },
  browser: { x: 864, y: 416 },
  file: { x: 608, y: 672 },
  search: { x: 864, y: 672 }
} as const;

const officeWorkSeats: Record<string, OfficeFootPoint> = {
  pm: workstationFootPoint(workstationSlots.pm, true),
  app: workstationFootPoint(workstationSlots.app),
  computer: workstationFootPoint(workstationSlots.computer),
  browser: workstationFootPoint(workstationSlots.browser),
  file: workstationFootPoint(workstationSlots.file),
  search: workstationFootPoint(workstationSlots.search),
  safety: { x: 1030, y: 382 }
};

export const officeAgents: OfficeAgentDefinition[] = [
  {
    id: "pm",
    name: "Lengrvis",
    role: "主控调度",
    icon: Sparkles,
    prompt: "帮我把今天的电脑任务拆成一个安全执行计划",
    accent: "#ff5474",
    glow: "rgba(255, 84, 116, 0.32)",
    x: officeWorkSeats.pm.x,
    y: officeWorkSeats.pm.y,
    wanderX: 1.2,
    wanderY: 1,
    delay: 0,
    duration: 5.4,
    scale: "lead",
    activities: ["坐镇调度", "拆解目标", "派发任务"]
  },
  {
    id: "file",
    name: "文件 Agent",
    role: "文件专家",
    icon: FolderOpen,
    prompt: "找出重复文件，但先不要删除",
    accent: "#8b5cf6",
    glow: "rgba(139, 92, 246, 0.32)",
    x: officeWorkSeats.file.x,
    y: officeWorkSeats.file.y,
    wanderX: 1.5,
    wanderY: 1.3,
    delay: 0.7,
    duration: 6,
    activities: ["检索文档", "扫描重复", "整理素材"]
  },
  {
    id: "computer",
    name: "电脑 Agent",
    role: "电脑管家",
    icon: Laptop,
    prompt: "查电脑配置",
    accent: "#f5a623",
    glow: "rgba(245, 166, 35, 0.32)",
    x: officeWorkSeats.computer.x,
    y: officeWorkSeats.computer.y,
    wanderX: 1.5,
    wanderY: 1.3,
    delay: 1.2,
    duration: 5.8,
    activities: ["读取配置", "巡检状态", "定位问题"]
  },
  {
    id: "app",
    name: "应用 Agent",
    role: "应用调度",
    icon: AppWindow,
    prompt: "帮我打开常用办公应用并列出可自动化的任务",
    accent: "#ff7e3e",
    glow: "rgba(255, 126, 62, 0.32)",
    x: officeWorkSeats.app.x,
    y: officeWorkSeats.app.y,
    wanderX: 1.5,
    wanderY: 1.3,
    delay: 1.8,
    duration: 6.5,
    activities: ["查找应用", "准备调用", "同步窗口"]
  },
  {
    id: "browser",
    name: "浏览器 Agent",
    role: "浏览器助手",
    icon: Globe2,
    prompt: "打开浏览器只读搜索最近的 AI 办公资料",
    accent: "#20bcd5",
    glow: "rgba(32, 188, 213, 0.32)",
    x: officeWorkSeats.browser.x,
    y: officeWorkSeats.browser.y,
    wanderX: 1.5,
    wanderY: 1.3,
    delay: 2.2,
    duration: 5.7,
    activities: ["读取网页", "等待授权", "整理链接"]
  },
  {
    id: "search",
    name: "搜索 Agent",
    role: "搜索专家",
    icon: Search,
    prompt: "搜索本地和网页资料，整理成三条可靠结论",
    accent: "#b87a4d",
    glow: "rgba(184, 122, 77, 0.32)",
    x: officeWorkSeats.search.x,
    y: officeWorkSeats.search.y,
    wanderX: 1.7,
    wanderY: 1.2,
    delay: 2.8,
    duration: 6.2,
    activities: ["喝咖啡", "交叉搜索", "比对来源"]
  },
  {
    id: "safety",
    name: "安全审核 Agent",
    role: "全程监督",
    icon: ShieldCheck,
    prompt: "先审核这个任务的风险等级，再告诉我是否需要审批",
    accent: "#4a6cf7",
    glow: "rgba(74, 108, 247, 0.32)",
    x: officeWorkSeats.safety.x,
    y: officeWorkSeats.safety.y,
    wanderX: 4,
    wanderY: 2,
    delay: 3.3,
    duration: 6.8,
    activities: ["巡逻审核", "扫描风险", "保护隐私"]
  }
];

const leisureSpots: LeisureSpot[] = [
  { pose: "coffee", x: 360, y: 184, activity: "喝咖啡", bounds: { minX: 340, maxX: 382, minY: 156, maxY: 214 }, jitter: { x: 8, y: 12 } },
  { pose: "coffee", x: 430, y: 284, activity: "等咖啡", bounds: { minX: 416, maxX: 456, minY: 262, maxY: 304 }, jitter: { x: 8, y: 10 } },
  { pose: "treadmill", x: 300, y: 474, activity: "跑步机训练", allowFurniture: "treadmill" },
  { pose: "wander", x: 468, y: 382, activity: "在走道协作", bounds: { minX: 430, maxX: 494, minY: 326, maxY: 520 }, jitter: { x: 12, y: 18 } },
  { pose: "restroom", x: 224, y: 790, activity: "洗手间中", allowFurniture: "toilet" },
  { pose: "nap", x: 472, y: 610, activity: "休息一下", bounds: { minX: 430, maxX: 506, minY: 548, maxY: 640 }, jitter: { x: 10, y: 14 } }
];

const safetyPatrolRoute = [
  { x: 1030, y: 382 },
  { x: 1030, y: 536 },
  { x: 1030, y: 706 },
  { x: 1000, y: 820 },
  { x: 1030, y: 286 }
];

export function createOfficeAgentState(
  agents: OfficeAgentDefinition[],
  workingAgentIdOrIds: string | ReadonlySet<string>,
  shouldWander: boolean
): Record<string, OfficeAgentRuntime> {
  const leisureOffset = Math.floor(Math.random() * leisureSpots.length);
  let idleIndex = 0;
  const workingAgentIds = typeof workingAgentIdOrIds === "string"
    ? new Set<string>([workingAgentIdOrIds])
    : workingAgentIdOrIds;

  return Object.fromEntries(
    agents.map((agent) => {
      if (workingAgentIds.has(agent.id) || !shouldWander) {
        const seat = officeWorkSeats[agent.id] ?? { x: agent.x, y: agent.y };
        return [
          agent.id,
          {
            x: seat.x,
            y: seat.y,
            activity: agent.id === "safety" ? "安全巡检中" : "坐在办公桌前敲击键盘",
            pose: "working" as OfficeAgentPose
          }
        ];
      }

      if (agent.id === "safety") {
        const routeIndex = Math.floor(Date.now() / 14000) % safetyPatrolRoute.length;
        const point = safetyPatrolRoute[routeIndex] ?? safetyPatrolRoute[0];
        return [
          agent.id,
          {
            x: point.x,
            y: point.y,
            activity: "巡逻审核",
            pose: "review" as OfficeAgentPose
          }
        ];
      }

      const spot = leisureSpots[(idleIndex + leisureOffset) % leisureSpots.length] ?? leisureSpots[0];
      const point = leisureFootPoint(spot);
      idleIndex += 1;
      return [
        agent.id,
        {
          x: point.x,
          y: point.y,
          activity: spot.activity,
          pose: spot.pose
        }
      ];
    })
  );
}

export function inferActiveOfficeAgentId(
  tasks: TaskEvent[],
  plan: Plan,
  conversations: AgentConversation[],
  safetyStatus: SafetyReview["status"]
): string {
  if (safetyStatus === "needs_review" || safetyStatus === "blocked") {
    return "safety";
  }

  const activeTask = tasks.find(
    (task) => task.state === "running" || task.state === "queued" || task.state === "blocked" || task.state === "paused"
  );
  const activeStep = plan.steps.find((step) => step.state === "active" || step.state === "blocked");
  const latestAgentMessage = conversations
    .flatMap((conversation) => conversation.messages)
    .filter((message) => message.agent || message.name)
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))[0];

  return (
    agentIdFromText(activeTask?.agent) ||
    agentIdFromText(activeStep?.owner) ||
    agentIdFromText(latestAgentMessage?.agent || latestAgentMessage?.name) ||
    "pm"
  );
}

export function projectOfficePoint(x: number, y: number, mapSize: OfficeMapSize) {
  if (mapSize.width <= 0 || mapSize.height <= 0) {
    return { x: 0, y: 0 };
  }

  const scale = Math.min(mapSize.width / officeViewBox.width, mapSize.height / officeViewBox.height);
  const renderedWidth = officeViewBox.width * scale;
  const renderedHeight = officeViewBox.height * scale;
  const offsetX = (mapSize.width - renderedWidth) / 2;
  const offsetY = (mapSize.height - renderedHeight) / 2;

  return {
    x: offsetX + x * scale,
    y: offsetY + y * scale
  };
}

export function activeOfficeAgentIds(workingAgentId: string, tasks: TaskEvent[], safetyAlert: boolean): Set<string> {
  const activeIds = new Set<string>([workingAgentId || "pm"]);

  for (const task of tasks) {
    if (task.state !== "running" && task.state !== "queued" && task.state !== "blocked" && task.state !== "paused") continue;
    const agentId = agentIdFromText(task.agent) || agentIdFromText(task.title) || agentIdFromText(task.description);
    if (agentId) activeIds.add(agentId);
  }

  if (safetyAlert || activeIds.has("safety")) {
    activeIds.add("safety");
  }

  return activeIds;
}

function agentIdFromText(value?: string) {
  const normalized = (value ?? "").toLowerCase();
  if (!normalized) return "";
  const localized = zhAgentName(value);
  if (normalized.includes("safety") || normalized.includes("human") || localized.includes("安全")) return "safety";
  if (normalized.includes("computer") || normalized.includes("system") || localized.includes("电脑")) return "computer";
  if (normalized.includes("browser") || localized.includes("浏览器")) return "browser";
  if (normalized.includes("search") || localized.includes("搜索")) return "search";
  if (
    normalized.includes("document") ||
    normalized.includes("file") ||
    normalized.includes("index") ||
    localized.includes("文件") ||
    localized.includes("文档") ||
    localized.includes("索引")
  ) {
    return "file";
  }
  if (normalized.includes("app") || localized.includes("应用")) return "app";
  if (
    normalized.includes("planner") ||
    normalized.includes("orchestrator") ||
    normalized.includes("pm") ||
    localized.includes("规划") ||
    localized.includes("调度")
  ) {
    return "pm";
  }
  return "";
}

function workstationFootPoint(slot: OfficeFootPoint, isBoss = false): OfficeFootPoint {
  const chairLeft = isBoss ? workstationBossChairLeft : workstationChairLeft;
  const chairWidth = isBoss ? workstationBossChairWidth : workstationChairWidth;

  return {
    x: slot.x - workstationFrameWidth / 2 + chairLeft + chairWidth / 2,
    y: slot.y + workstationFootYOffset
  };
}

function leisureFootPoint(spot: LeisureSpot): OfficeFootPoint {
  if (!spot.bounds || !spot.jitter) {
    return { x: spot.x, y: spot.y };
  }

  const point = {
    x: clamp(spot.x + randomBetween(-spot.jitter.x, spot.jitter.x), spot.bounds.minX, spot.bounds.maxX),
    y: clamp(spot.y + randomBetween(-spot.jitter.y, spot.jitter.y), spot.bounds.minY, spot.bounds.maxY)
  };

  if (isSafeFootPoint(point, spot.allowFurniture)) {
    return point;
  }

  return { x: spot.x, y: spot.y };
}

function isSafeFootPoint(point: OfficeFootPoint, allowFurniture?: StaticFurnitureName): boolean {
  if (point.x < 0 || point.x > officeViewBox.width || point.y < 0 || point.y > officeViewBox.height) {
    return false;
  }

  return !Object.entries(staticFurniture).some(
    ([name, rect]) => name !== allowFurniture && pointIsInsideRect(point, rect)
  );
}

function pointIsInsideRect(point: OfficeFootPoint, rect: FurnitureRect) {
  return point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height;
}

function randomBetween(min: number, max: number) {
  return min + Math.random() * (max - min);
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}
