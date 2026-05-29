import { ArrowRight } from "lucide-react";
import type { CSSProperties } from "react";

import type { SafetyReview, TaskEvent } from "../../../shared/types";
import { Badge } from "../../components/Panel";
import { zhTaskState } from "../../lib/zh";
import { officeAgents, type OfficeAgentDefinition } from "../office";

interface AgentOpsViewProps {
  tasks: TaskEvent[];
  safetyReview: SafetyReview;
  onDraftPrompt: (prompt: string) => void;
  onOpenChat: () => void;
  onOpenApprovals: () => void;
}

type AgentOpsStatus = "active" | "waiting" | "idle";

const agentOverviewCopy: Record<string, { title: string; helper: string; prompt: string }> = {
  pm: {
    title: "Mavris",
    helper: "待命，负责规划与协调",
    prompt: "帮我处理一个电脑任务"
  },
  file: {
    title: "文件",
    helper: "待命，可在对话中协作查找文件",
    prompt: "帮我找文件或整理文件，修改前先让我确认"
  },
  computer: {
    title: "电脑",
    helper: "待命，可在对话中协作检查电脑",
    prompt: "帮我检查这台电脑的状态"
  },
  app: {
    title: "应用",
    helper: "待命，可在对话中协作处理应用",
    prompt: "帮我打开或操作应用"
  },
  browser: {
    title: "浏览器",
    helper: "待命，可在对话中协作处理网页",
    prompt: "帮我用浏览器处理网页任务"
  },
  search: {
    title: "搜索",
    helper: "待命，可在对话中协作查资料",
    prompt: "帮我搜索资料并整理结论"
  },
  safety: {
    title: "安全",
    helper: "暂无待审批操作",
    prompt: "帮我检查当前任务是否需要确认"
  }
};

const agentKeywords: Record<string, string[]> = {
  pm: ["orchestrator", "planner", "supervisor", "execution", "调度", "规划", "主管", "执行"],
  file: ["file", "document", "index", "cleanup", "文件", "文档", "目录", "索引", "清理"],
  computer: ["computer", "system", "电脑", "系统", "设备", "配置"],
  app: ["app", "application", "应用", "软件", "程序"],
  browser: ["browser", "web", "网页", "浏览器", "页面"],
  search: ["search", "query", "搜索", "查询", "资料"],
  safety: ["safety", "human", "approval", "安全", "审批", "审核"]
};

export function AgentOpsView({
  tasks,
  safetyReview,
  onDraftPrompt,
  onOpenChat,
  onOpenApprovals
}: AgentOpsViewProps) {
  const activeTasks = tasks.filter((task) => task.state === "running" || task.state === "queued" || task.state === "paused");
  const blockedTasks = tasks.filter((task) => task.state === "blocked");
  const needsApproval = blockedTasks.length > 0 || safetyReview.status === "needs_review" || safetyReview.status === "blocked";
  const summary = agentOpsSummary(activeTasks.length, needsApproval);

  const openAgentEntry = (agent: OfficeAgentDefinition) => {
    if (agent.id === "safety" && needsApproval) {
      onOpenApprovals();
      return;
    }

    onDraftPrompt(agentOverviewCopy[agent.id]?.prompt ?? agent.prompt);
    onOpenChat();
  };

  return (
    <div className="tool-launcher-surface">
      <div className="tool-launcher-summary">
        <div>
          <span className="panel__eyebrow">Agent 总览</span>
          <h2>协作状态</h2>
          <p>这里展示真实任务和审批状态，具体操作从对话或审批入口继续。</p>
        </div>
        <Badge tone={needsApproval ? "warning" : activeTasks.length ? "info" : "neutral"}>{summary}</Badge>
      </div>

      <div className="tool-launcher-grid" aria-label="Agent 协作状态">
        {officeAgents.map((agent) => {
          const status = agentStatus(agent, tasks, safetyReview);
          const Icon = agent.icon;
          const copy = agentOverviewCopy[agent.id] ?? { title: agent.name, helper: agent.role, prompt: agent.prompt };
          const isApprovalEntry = agent.id === "safety" && needsApproval;
          return (
            <article
              className={`tool-launcher-card tool-launcher-card--${status}`}
              key={agent.id}
            >
              <span className="tool-launcher-card__icon" style={{ "--agent-accent": agent.accent } as CSSProperties}>
                <Icon size={18} aria-hidden="true" />
              </span>
              <span className="tool-launcher-card__copy">
                <strong>{copy.title}</strong>
                <small>{toolStatusText(agent, status, tasks, copy.helper)}</small>
              </span>
              <span className="tool-launcher-card__meta">
                {status !== "idle" ? (
                  <Badge tone={status === "waiting" ? "warning" : "info"}>
                    {status === "waiting" ? "需确认" : "处理中"}
                  </Badge>
                ) : null}
                <button
                  className="button button--small button--secondary tool-launcher-card__cta"
                  type="button"
                  onClick={() => openAgentEntry(agent)}
                >
                  {isApprovalEntry ? "查看审批" : "去对话"}
                  <ArrowRight size={16} aria-hidden="true" />
                </button>
              </span>
            </article>
          );
        })}
      </div>

      <div className={`tool-status-strip ${needsApproval ? "tool-status-strip--warning" : ""}`}>
        <span>
          <strong>{needsApproval ? "有操作需要审批" : activeTasks.length ? `正在协作 ${activeTasks.length} 个任务` : "暂无任务"}</strong>
          {needsApproval ? "请先查看审批，再继续相关任务。" : activeTasks[0]?.title ?? "所有 Agent 当前待命，开始对话后会显示真实协作状态。"}
        </span>
        <button
          className="button button--small button--secondary"
          type="button"
          onClick={needsApproval ? onOpenApprovals : onOpenChat}
        >
          {needsApproval ? "查看审批" : "去对话"}
        </button>
      </div>
    </div>
  );
}

function agentOpsSummary(activeCount: number, needsApproval: boolean) {
  if (needsApproval) return "待审批";
  if (activeCount) return `${activeCount} 项协作中`;
  return "待命";
}

function agentStatus(
  agent: OfficeAgentDefinition,
  tasks: TaskEvent[],
  safetyReview: SafetyReview
): AgentOpsStatus {
  if (agent.id === "safety" && (safetyReview.status === "needs_review" || safetyReview.status === "blocked")) {
    return "waiting";
  }

  const activeTask = tasks.find((task) =>
    (task.state === "running" || task.state === "queued" || task.state === "blocked" || task.state === "paused") &&
    matchesAgent(agent.id, task.agent, task.title, task.description)
  );
  if (activeTask?.state === "blocked") return "waiting";
  if (activeTask) return "active";

  return "idle";
}

function toolStatusText(agent: OfficeAgentDefinition, status: AgentOpsStatus, tasks: TaskEvent[], fallback: string) {
  const task = tasks.find((item) =>
    (item.state === "running" || item.state === "queued" || item.state === "blocked" || item.state === "paused") &&
    matchesAgent(agent.id, item.agent, item.title, item.description)
  );
  if (task) return `${zhTaskState(task.state)}：${task.title}`;
  if (status === "waiting") return "等待你审批";
  return fallback;
}

function matchesAgent(agentId: string, ...values: Array<string | undefined>) {
  const keywords = agentKeywords[agentId] ?? [];
  const text = values.filter(Boolean).join(" ").toLowerCase();
  return keywords.some((keyword) => text.includes(keyword.toLowerCase()));
}
