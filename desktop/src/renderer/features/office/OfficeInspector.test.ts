import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Sparkles } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

import type { OfficeTaskPresentation } from "./officeTaskPresentation";
import { OfficeInspector } from "./OfficeInspector";
import type { HomeReadinessItem, HomeTrustItem, OfficeQuickSkill } from "./OfficeScene";

const quickSkill: OfficeQuickSkill = {
  id: "check-computer",
  kind: "action",
  action: "system-check",
  icon: Sparkles,
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

const presentation: OfficeTaskPresentation = {
  currentTasks: [],
  displayedTasks: [
    {
      id: "task-1",
      title: "检查电脑状态",
      description: "读取系统快照",
      state: "running",
      agent: "computer",
      createdAt: "2026-07-03T01:00:00.000Z",
      updatedAt: "2026-07-03T02:00:00.000Z"
    }
  ],
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
  taskWorkspaceItems: [],
  outcomeCards: []
};

describe("OfficeInspector", () => {
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
    expect(html).toContain("检查电脑状态");
    expect(html).toContain("1/1 已就绪");
  });
});
