import {
  CheckCircle2,
  Clock,
  FileText,
  FolderOpen,
  LockKeyhole,
  Radio,
  ShieldCheck,
  Sparkles,
  type LucideIcon
} from "lucide-react";

import type { OfficeTaskPresentation } from "./officeTaskPresentation";
import { taskDisplayState, taskDisplayTitle } from "./officeTaskPresentation";
import type {
  HomeReadinessItem,
  HomeTrustItem,
  OfficeQuickSkill,
  OfficeSceneProps
} from "./OfficeScene";

interface OfficeInspectorProps {
  quickSkills: OfficeQuickSkill[];
  selectedQuickSkill: OfficeQuickSkill | null;
  readinessItems: HomeReadinessItem[];
  trustItems: HomeTrustItem[];
  presentation: OfficeTaskPresentation;
  pendingApprovalCount: number;
  safetyAlert: boolean;
  onQuickSkillClick: (skill: OfficeQuickSkill) => void;
  onReadinessAction: OfficeSceneProps["onReadinessAction"];
  onTaskPilotAction: OfficeSceneProps["onTaskPilotAction"];
}

interface HomeStatusChip {
  id: "connection" | "ai" | "files" | "approval";
  label: string;
  value: string;
  detail: string;
  tone: "ready" | "warning" | "blocked";
  icon: LucideIcon;
}

export function OfficeInspector({
  quickSkills,
  selectedQuickSkill,
  readinessItems,
  trustItems,
  presentation,
  pendingApprovalCount,
  safetyAlert,
  onQuickSkillClick,
  onReadinessAction,
  onTaskPilotAction
}: OfficeInspectorProps) {
  const statusChips = buildHomeStatusChips(readinessItems, trustItems, pendingApprovalCount, safetyAlert);
  const {
    currentTasks,
    displayedTasks,
    activeTaskLabel,
    recentTaskLabel,
    blockedTaskCount,
    taskPilot,
    resultTimeline,
    taskWorkspaceItems,
    outcomeCards
  } = presentation;

  return (
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
              onClick={() => onQuickSkillClick(skill)}
              aria-pressed={selectedQuickSkill?.id === skill.id}
            >
              <span className="office-quick-card__icon" aria-hidden="true" />
              <span className="office-quick-card__body">
                <span className="office-quick-card__title">
                  <strong>{skill.title}</strong>
                  <em>{skill.summary || quickSkillHint(skill)}</em>
                </span>
                <span className="office-quick-card__details">
                  <em>产出：{skill.wizard.output}</em>
                  <em>边界：{skill.wizard.preflight}</em>
                </span>
                <span className="office-quick-card__meta">
                  <b>{skill.trust.estimate}</b>
                  <b>{skill.trust.approval}</b>
                </span>
                <small className="office-quick-card__trust">
                  <b>{skill.trust.local}</b>
                  <b>{skill.trust.cloud}</b>
                  <b>{skill.trust.rollback}</b>
                </small>
              </span>
              <span className="office-quick-card__action" aria-hidden="true">
                {selectedQuickSkill?.id === skill.id
                  ? skill.kind === "view" ? "已打开" : "已选择 · 点发送"
                  : skill.kind === "view" ? "打开查看" : "点发送开始"}
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
              <span>{selectedQuickSkill.kind === "prompt" ? "已填入输入框" : selectedQuickSkill.kind === "view" ? "等待打开工具区" : "等待开始"}</span>
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
            <strong>{currentTasks.length > 0 ? "已开始处理任务" : "空闲"}</strong>
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
          <em>结果核验、隐私权限、任务驾驶舱与成果</em>
        </summary>
        <div className="home-more__cards">
          <div className={`inspector-card result-timeline-card result-timeline-card--${resultTimeline.tone}`} data-testid="home-result-timeline-card">
            <div className="inspector-card__head">
              <strong>结果时间线</strong>
              <span>{resultTimeline.statusLabel}</span>
            </div>
            <div className="result-timeline-card__summary">
              <strong>{resultTimeline.title}</strong>
              <p>{resultTimeline.detail}</p>
              {resultTimeline.missingChecks.length ? (
                <em>缺少：{resultTimeline.missingChecks.slice(0, 2).join("、")}；下一步：{resultTimeline.nextStep}</em>
              ) : (
                <em>{resultTimeline.canTreatAsDone ? "结果可以作为完成记录" : resultTimeline.privacyNote}</em>
              )}
            </div>
            <div className="result-timeline-steps" aria-label="任务结果时间线">
              {resultTimeline.steps.map((step) => {
                const Icon = resultTimelineStepIcon(step.id);
                return (
                  <span key={step.id} className={`result-timeline-step result-timeline-step--${step.state}`}>
                    <Icon size={13} aria-hidden="true" />
                    <b>{step.label}</b>
                    <em>{step.detail}</em>
                  </span>
                );
              })}
            </div>
            <button
              className="task-pilot-action result-timeline-card__action"
              type="button"
              onClick={() => onTaskPilotAction?.(resultTimeline.task, resultTimeline.action)}
            >
              {resultTimeline.action === "approve" ? <ShieldCheck size={14} aria-hidden="true" /> : resultTimeline.action === "open" ? <Radio size={14} aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}
              {resultTimeline.actionLabel}
            </button>
          </div>

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
  );
}

function quickSkillHint(skill: OfficeQuickSkill): string {
  if (skill.kind === "view" && skill.id === "summarize-document") return "打开文档操作区";
  return skill.kind === "prompt" ? "填好后点发送" : "打开页面";
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

function resultTimelineStepIcon(stepId: "understand" | "scope" | "execute" | "verify"): LucideIcon {
  if (stepId === "understand") return Sparkles;
  if (stepId === "scope") return LockKeyhole;
  if (stepId === "execute") return Radio;
  return CheckCircle2;
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
