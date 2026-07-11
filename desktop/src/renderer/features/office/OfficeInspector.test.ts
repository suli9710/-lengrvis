import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { OfficeTaskPresentation } from "./officeTaskPresentation";
import { OfficeInspector } from "./OfficeInspector";
import type { HomeReadinessItem, HomeTrustItem, OfficeQuickSkill } from "./OfficeScene";
import { buildTaskResultTimelineSummary } from "../task-results/taskResultTimeline";

const quickSkill: OfficeQuickSkill = {
  id: "check-computer",
  kind: "prompt",
  prompt: "帮我检查这台电脑",
  title: "检查电脑",
  summary: "只读检查",
  trust: {
    local: "本机",
    cloud: "不上传",
    approval: "无需审批",
    rollback: "无改动",
    estimate: "约 1 分钟"
  },
  wizard: {
    input: "系统状态",
    preflight: "只读读取",
    output: "健康摘要",
    nextStep: "等待检查结果"
  }
};

const readinessItems: HomeReadinessItem[] = [
  {
    id: "connection",
    label: "连接",
    detail: "后端已连接",
    state: "ready",
    actionLabel: "查看"
  }
];

const trustItems: HomeTrustItem[] = [
  {
    id: "files",
    label: "文件",
    value: "仅所选范围",
    detail: "不会读取范围外文件",
    state: "ready"
  }
];

const displayedTask = {
  id: "task-1",
  title: "检查电脑状态",
  description: "读取系统快照",
  state: "running" as const,
  agent: "computer",
  createdAt: "2026-07-03T01:00:00.000Z",
  updatedAt: "2026-07-03T02:00:00.000Z"
};

const presentation: OfficeTaskPresentation = {
  currentTasks: [displayedTask],
  displayedTasks: [displayedTask],
  activeTaskLabel: "当前没有正在处理的任务",
  recentTaskLabel: "显示最近 1 项",
  blockedTaskCount: 0,
  runningTaskCount: 1,
  taskPilot: {
    title: "检查电脑状态",
    detail: "读取系统快照",
    status: "执行中",
    tone: "active",
    action: "open",
    actionLabel: "查看进度",
    task: null,
    steps: []
  },
  resultTimeline: buildTaskResultTimelineSummary([displayedTask]),
  taskWorkspaceItems: [],
  outcomeCards: []
};

describe("OfficeInspector", () => {
  it("discloses how to start, expected output, and safety boundaries before selection", () => {
    const html = renderToStaticMarkup(createElement(OfficeInspector, {
      quickSkills: [quickSkill],
      selectedQuickSkill: null,
      readinessItems,
      trustItems,
      presentation,
      pendingApprovalCount: 0,
      safetyAlert: false,
      onQuickSkillClick: vi.fn(),
      onReadinessAction: vi.fn(),
      onTaskPilotAction: vi.fn()
    }));

    expect(html).toContain("点发送开始");
    expect(html).toContain("产出：健康摘要");
    expect(html).toContain("边界：只读读取");
    expect(html).toContain("本机");
    expect(html).toContain("不上传");
    expect(html).toContain("无改动");
  });

  it("renders the template wizard, status strip, and recent task through one interface", () => {
    const html = renderToStaticMarkup(createElement(OfficeInspector, {
      quickSkills: [quickSkill],
      selectedQuickSkill: quickSkill,
      readinessItems,
      trustItems,
      presentation,
      pendingApprovalCount: 0,
      safetyAlert: false,
      onQuickSkillClick: vi.fn(),
      onReadinessAction: vi.fn(),
      onTaskPilotAction: vi.fn()
    }));

    expect(html).toContain('data-testid="office-template-check-computer"');
    expect(html).toContain('data-testid="office-template-wizard"');
    expect(html).toContain('data-testid="home-status-strip"');
    expect(html).toContain("<strong>结果时间线</strong>");
    expect(html).toContain("已开始处理任务");
    expect(html).toContain("检查电脑状态");
    expect(html).toContain("1/1 已就绪");
  });
});
