import { CheckCircle2, ChevronLeft, ChevronRight, ShieldCheck, Trash2, Undo2, XCircle } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";

import type { CleanupPlan } from "../../shared/cleanupTypes";
import type { ApprovalRequest } from "../../shared/executionTypes";
import { zhAgentName, zhSeverity } from "../lib/zh";
import { Badge } from "./Panel";

interface ApprovalDialogProps {
  approval: ApprovalRequest | null;
  pendingCount?: number;
  selectionContext?: "task" | "queue";
  queueIndex?: number;
  canGoPrevious?: boolean;
  canGoNext?: boolean;
  isOpen: boolean;
  error?: string | null;
  onClose: () => void;
  onPrevious?: () => void;
  onNext?: () => void;
  onDecision: (approvalId: string, decision: "approved" | "denied", note?: string) => Promise<void>;
}

export function ApprovalDialog({
  approval,
  pendingCount = 0,
  selectionContext = "task",
  queueIndex = 0,
  canGoPrevious = false,
  canGoNext = false,
  isOpen,
  error,
  onClose,
  onPrevious,
  onNext,
  onDecision
}: ApprovalDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocusedElement = useRef<HTMLElement | null>(null);
  const [note, setNote] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    setNote("");
  }, [approval?.id]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    previouslyFocusedElement.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const timerId = window.setTimeout(() => {
      firstFocusableElement(dialogRef.current)?.focus() ?? dialogRef.current?.focus();
    }, 0);

    return () => {
      window.clearTimeout(timerId);
      previouslyFocusedElement.current?.focus();
      previouslyFocusedElement.current = null;
    };
  }, [isOpen, approval?.id]);

  if (!isOpen || !approval) {
    return null;
  }

  const cleanupPlan = approval.cleanupPlan;
  const cleanupGroups = splitCleanupItems(cleanupPlan);
  const decisionSummary = buildDecisionSummary(approval, cleanupPlan, cleanupGroups);
  const subtitle = approvalSubtitle(approval, selectionContext, pendingCount, queueIndex);
  const canDecide = approval.status === "pending";

  const decide = async (decision: "approved" | "denied") => {
    if (!canDecide) return;
    setIsSubmitting(true);
    try {
      await onDecision(approval.id, decision, note.trim() || undefined);
      setNote("");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-title"
        tabIndex={-1}
        ref={dialogRef}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            if (isSubmitting) {
              event.preventDefault();
              return;
            }
            onClose();
            return;
          }
          if (event.key === "Tab") {
            trapFocus(event, dialogRef.current);
          }
        }}
      >
        <header className="modal__header">
          <div>
            <span className="panel__eyebrow">审批</span>
            <h2 id="approval-title">{approval.title}</h2>
            {subtitle ? <p className="modal__subtitle">{subtitle}</p> : null}
          </div>
          <Badge tone={approval.riskLevel === "high" || approval.riskLevel === "critical" ? "danger" : "warning"}>
            {zhSeverity(approval.riskLevel)}
          </Badge>
        </header>
        {selectionContext === "queue" && pendingCount > 1 ? (
          <nav className="approval-queue-nav" aria-label="审批队列导航">
            <button className="button button--ghost" type="button" onClick={onPrevious} disabled={isSubmitting || !canGoPrevious}>
              <ChevronLeft size={16} aria-hidden="true" />
              上一项
            </button>
            <span>第 {queueIndex || 1} / {pendingCount} 项</span>
            <button className="button button--ghost" type="button" onClick={onNext} disabled={isSubmitting || !canGoNext}>
              下一项
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          </nav>
        ) : null}
        <div className="modal__body">
          <ApprovalDecisionOverview summary={decisionSummary} />
          {cleanupPlan ? (
            <CleanupApprovalPreview
              plan={cleanupPlan}
              permanent={cleanupGroups.permanent}
              trash={cleanupGroups.trash}
              suggestions={cleanupGroups.suggestions}
            />
          ) : null}
          <ApprovalSafetyChecklist summary={buildSafetyChecklist(approval, cleanupPlan, cleanupGroups)} />
          <details className="approval-tech-details">
            <summary className="approval-tech-details__summary">
              查看技术细节（工具、参数与运行时边界）
            </summary>
            <ApprovalEngineeringBoundary approval={approval} />
          </details>
          <dl className="detail-list">
            <div>
              <dt>任务</dt>
              <dd>{approval.taskId || "未关联任务"}</dd>
            </div>
            <div>
              <dt>步骤</dt>
              <dd>{approval.stepId || "未关联步骤"}</dd>
            </div>
            <div>
              <dt>请求方</dt>
              <dd>{zhAgentName(approval.requester)}</dd>
            </div>
            <div>
              <dt>原因</dt>
              <dd>{approval.reason}</dd>
            </div>
            <div>
              <dt>动作</dt>
              <dd>{approval.proposedAction}</dd>
            </div>
          </dl>
          <label className="field">
            <span>审批备注</span>
            <textarea value={note} onChange={(event) => setNote(event.target.value)} rows={4} />
            {error ? <p className="field-error" role="alert">{error}</p> : null}
          </label>
        </div>
        <footer className="modal__footer">
          <button className="button button--ghost" onClick={onClose} disabled={isSubmitting}>
            取消
          </button>
          {canDecide ? (
            <>
              <button className="button button--danger" onClick={() => void decide("denied")} disabled={isSubmitting}>
                <XCircle size={16} aria-hidden="true" />
                拒绝
              </button>
              <button className="button button--primary" onClick={() => void decide("approved")} disabled={isSubmitting}>
                <CheckCircle2 size={16} aria-hidden="true" />
                批准
              </button>
            </>
          ) : null}
        </footer>
      </div>
    </div>
  );
}

function ApprovalSafetyChecklist({ summary }: { summary: ApprovalSafetyChecklistSummary }) {
  return (
    <section className={`approval-safety approval-safety--${summary.tone}`} aria-label="安全核对">
      <div className="approval-safety__header">
        <span>安全核对</span>
        <strong>{summary.title}</strong>
      </div>
      <p>{summary.detail}</p>
      <div className="approval-safety__grid">
        {summary.items.map((item) => (
          <span key={item.label}>
            <em>{item.label}</em>
            <strong>{item.value}</strong>
          </span>
        ))}
      </div>
    </section>
  );
}

function ApprovalEngineeringBoundary({ approval }: { approval: ApprovalRequest }) {
  const boundary = objectValue(approval.engineeringBoundary);
  const boundaryTool = objectValue(boundary.tool);
  const boundaryDryRun = objectValue(boundary.dry_run);
  const boundaryBinding = objectValue(boundary.binding);
  const modelAction = approval.modelAction ?? objectValue(boundary.model_action);
  const runtimeFields = approval.runtimeControlFields ?? objectValue(boundary.runtime_fields);
  const toolEffects = approval.toolEffects?.length ? approval.toolEffects : stringList(boundaryTool.effects);
  const resourceKinds = approval.resourceKinds?.length ? approval.resourceKinds : stringList(boundaryTool.resource_kinds);
  const toolName = approval.toolName || textValue(boundaryTool.name) || textValue(modelAction.tool_name);
  const trustTier = approval.toolTrustTier || textValue(boundaryTool.trust_tier);
  const policyMode = approval.policyMode || textValue(boundary.policy_mode);
  const dryRunSummary = approval.dryRunSummary || textValue(boundaryDryRun.summary);
  const previewKeys = stringList(boundaryDryRun.preview_keys);
  const modelFacts = [
    ["action_type", textValue(modelAction.action_type)],
    ["tool_name", textValue(modelAction.tool_name)],
    ["context_snapshot_id", textValue(modelAction.context_snapshot_id)],
    ["visible_tool_ids", visibleToolSummary(modelAction.visible_tool_ids)],
  ].filter(([, value]) => value);
  const runtimeFacts = Object.entries(runtimeFields)
    .map(([key, value]) => `${key}: ${textValue(value) || "runtime"}`)
    .filter(Boolean);
  const bindingFacts = Object.entries(boundaryBinding)
    .filter(([, value]) => Boolean(value))
    .map(([key]) => key.replace(/_/g, " "));

  return (
    <section className="approval-boundary" aria-label="工程边界">
      <div className="approval-boundary__header">
        <div>
          <span>工程边界</span>
          <strong>{toolName || "未绑定工具"}</strong>
        </div>
        <Badge tone={approval.riskLevel === "critical" || approval.riskLevel === "high" ? "danger" : "warning"}>
          {zhSeverity(approval.riskLevel)}
        </Badge>
      </div>
      <div className="approval-boundary__chips">
        <span>mode: {policyMode || "default"}</span>
        <span>trust: {trustTier || "untrusted"}</span>
        {toolEffects.map((effect) => <span key={`effect-${effect}`}>effect: {effect}</span>)}
        {resourceKinds.map((resource) => <span key={`resource-${resource}`}>resource: {resource}</span>)}
      </div>
      <div className="approval-boundary__grid">
        <BoundaryBlock title="Dry-run" lines={[dryRunSummary || "没有 dry-run 摘要", previewKeys.length ? `preview: ${previewKeys.join(", ")}` : ""]} />
        <BoundaryBlock title="模型字段" lines={modelFacts.map(([key, value]) => `${key}: ${value}`)} />
        <BoundaryBlock title="运行时字段" lines={runtimeFacts} />
        <BoundaryBlock title="参数绑定" lines={bindingFacts} />
      </div>
      {textValue(modelAction.model_reason) ? (
        <p className="approval-boundary__reason">{textValue(modelAction.model_reason)}</p>
      ) : null}
    </section>
  );
}

function BoundaryBlock({ title, lines }: { title: string; lines: string[] }) {
  const visibleLines = lines.filter((line) => line.trim());
  return (
    <div className="approval-boundary__block">
      <strong>{title}</strong>
      {visibleLines.length ? (
        <ul>
          {visibleLines.map((line) => <li key={line}>{line}</li>)}
        </ul>
      ) : (
        <p>未提供</p>
      )}
    </div>
  );
}

function ApprovalDecisionOverview({ summary }: { summary: ApprovalDecisionSummary }) {
  return (
    <section className={`approval-decision approval-decision--${summary.tone}`} aria-label="审批决策总览">
      <div className="approval-decision__main">
        <span>
          <ShieldCheck size={15} aria-hidden="true" />
          决策总览
        </span>
        <strong>{summary.title}</strong>
        <p>{summary.detail}</p>
      </div>
      <div className="approval-decision__facts">
        <span>
          <Trash2 size={14} aria-hidden="true" />
          <em>影响范围</em>
          <strong>{summary.scope}</strong>
        </span>
        <span>
          <Undo2 size={14} aria-hidden="true" />
          <em>恢复方式</em>
          <strong>{summary.recovery}</strong>
        </span>
        <span>
          <ShieldCheck size={14} aria-hidden="true" />
          <em>执行前</em>
          <strong>{summary.guard}</strong>
        </span>
      </div>
    </section>
  );
}

function approvalSubtitle(
  approval: ApprovalRequest,
  selectionContext: "task" | "queue",
  pendingCount: number,
  queueIndex: number
): string {
  if (selectionContext === "queue") {
    const position = queueIndex > 0 ? queueIndex : 1;
    return pendingCount > 1
      ? `审批队列第 ${position} / ${pendingCount} 项。请按任务和动作逐项确认。`
      : "审批队列中当前只有这一项，请确认任务和动作后再决定。";
  }
  if (pendingCount > 1) {
    return `当前显示与任务 ${approval.taskId || "未关联任务"} 匹配的一项；队列里共有 ${pendingCount} 项待确认。`;
  }
  return approval.taskId ? `当前审批关联任务 ${approval.taskId}。` : "";
}

function CleanupApprovalPreview({
  plan,
  permanent,
  trash,
  suggestions
}: {
  plan: CleanupPlan;
  permanent: CleanupPlan["items"];
  trash: CleanupPlan["items"];
  suggestions: CleanupPlan["items"];
}) {
  return (
    <section className="approval-cleanup">
      <div className="approval-cleanup__summary">
        <div>
          <span>预计释放</span>
          <strong>{formatBytes(plan.reclaimableBytes)}</strong>
        </div>
        <div>
          <span>永久删除</span>
          <strong>{permanent.length} 项</strong>
        </div>
        <div>
          <span>进回收站</span>
          <strong>{trash.length} 项</strong>
        </div>
      </div>
      <CleanupApprovalBucket
        title="永久删除"
        description="批准后会直接删除，通常不可从回收站恢复。"
        tone="danger"
        items={permanent}
        emptyText="没有永久删除项"
      />
      <CleanupApprovalBucket
        title="进回收站"
        description="批准后会移入回收站，仍可能手动恢复。"
        tone="warning"
        items={trash}
        emptyText="没有回收站项"
      />
      <CleanupApprovalBucket
        title="仅建议"
        description="这些项目只提示你检查，不会随本次批准删除。"
        tone="neutral"
        items={suggestions}
        emptyText="没有仅建议项"
      />
      {plan.riskWarnings.length ? (
        <div className="approval-cleanup__risks">
          <strong>风险提示</strong>
          <ul>
            {plan.riskWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function CleanupApprovalBucket({
  title,
  description,
  tone,
  items,
  emptyText
}: {
  title: string;
  description: string;
  tone: "neutral" | "warning" | "danger";
  items: CleanupPlan["items"];
  emptyText: string;
}) {
  return (
    <section className="approval-cleanup__bucket">
      <div className="row row--between">
        <div>
          <strong>{title}</strong>
          <p className="muted">{description}</p>
        </div>
        <Badge tone={tone}>{items.length} 项</Badge>
      </div>
      {items.length ? (
        <ul>
          {items.slice(0, 6).map((item) => (
            <li key={item.id}>
              <span>{item.path}</span>
              <em>{formatBytes(item.sizeBytes ?? (item.sizeMb ? item.sizeMb * 1024 * 1024 : undefined))}</em>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{emptyText}</p>
      )}
    </section>
  );
}

function splitCleanupItems(plan?: CleanupPlan) {
  const items = plan?.items ?? [];
  return {
    permanent: items.filter((item) => item.disposition === "permanent_delete"),
    trash: items.filter((item) => item.disposition === "trash"),
    suggestions: items.filter((item) => item.disposition !== "permanent_delete" && item.disposition !== "trash")
  };
}

interface ApprovalDecisionSummary {
  title: string;
  detail: string;
  scope: string;
  recovery: string;
  guard: string;
  tone: "safe" | "warning" | "danger";
}

interface ApprovalSafetyChecklistSummary {
  title: string;
  detail: string;
  tone: "safe" | "warning" | "danger";
  items: Array<{ label: string; value: string }>;
}

function buildSafetyChecklist(
  approval: ApprovalRequest,
  plan: CleanupPlan | undefined,
  groups: ReturnType<typeof splitCleanupItems>
): ApprovalSafetyChecklistSummary {
  const boundary = objectValue(approval.engineeringBoundary);
  const boundaryTool = objectValue(boundary.tool);
  const boundaryDryRun = objectValue(boundary.dry_run);
  const effects = approval.toolEffects?.length ? approval.toolEffects : stringList(boundaryTool.effects);
  const resources = approval.resourceKinds?.length ? approval.resourceKinds : stringList(boundaryTool.resource_kinds);
  const riskIsHigh = approval.riskLevel === "high" || approval.riskLevel === "critical";
  const hasPermanentDelete = groups.permanent.length > 0;
  const hasTrash = groups.trash.length > 0;
  const hasDryRun = Boolean(plan || approval.dryRunSummary || textValue(boundaryDryRun.summary));
  const effectSummary = approvalEffectSummary(effects);
  const resourceSummary = approvalResourceSummary(resources);
  const recovery = hasPermanentDelete
    ? "含不可恢复项"
    : hasTrash
      ? "可从回收站恢复"
      : plan
        ? "本次不直接删除"
        : "按动作决定";
  const preview = hasDryRun ? "已有执行前预览" : "未看到预览";
  const title = hasPermanentDelete || approval.riskLevel === "critical"
    ? "不确定就先拒绝"
    : riskIsHigh
      ? "只在完全确认后批准"
      : hasDryRun
        ? "核对无误后再批准"
        : "先要求更清楚的预览";
  const detail = hasPermanentDelete
    ? "这里包含永久删除或高影响动作。除非路径、数量、恢复方式都和你的目标一致，否则选择拒绝。"
    : riskIsHigh
      ? "这是高影响操作。请确认它只会作用在你期望的任务、文件或应用上。"
      : hasDryRun
        ? "系统已经停在审批点，批准前不会继续执行。请按影响范围和恢复方式核对。"
        : "当前信息偏少。看不懂动作、范围或后果时，拒绝是更安全的选择。";
  const tone = hasPermanentDelete || approval.riskLevel === "critical" ? "danger" : riskIsHigh || !hasDryRun ? "warning" : "safe";

  return {
    title,
    detail,
    tone,
    items: [
      { label: "影响", value: effectSummary },
      { label: "对象", value: resourceSummary },
      { label: "预览", value: preview },
      { label: "恢复", value: recovery }
    ]
  };
}

function approvalEffectSummary(effects: string[]): string {
  const normalized = effects.map((effect) => effect.toLowerCase());
  if (normalized.some((effect) => /delete|remove|clean|trash/.test(effect))) return "删除或清理";
  if (normalized.some((effect) => /write|modify|edit|update|create|move/.test(effect))) return "写入或修改";
  if (normalized.some((effect) => /input|click|type|ui|gui/.test(effect))) return "控制界面";
  if (normalized.some((effect) => /network|http|browser|web/.test(effect))) return "联网或浏览";
  if (normalized.some((effect) => /message|send|mail|chat/.test(effect))) return "发送内容";
  if (normalized.some((effect) => /read|open|list/.test(effect))) return "读取或打开";
  return effects.length ? effects.slice(0, 2).join(", ") : "未声明";
}

function approvalResourceSummary(resources: string[]): string {
  const normalized = resources.map((resource) => resource.toLowerCase());
  if (normalized.some((resource) => /file|folder|path|document/.test(resource))) return "文件或文档";
  if (normalized.some((resource) => /app|window|screen|desktop|ui/.test(resource))) return "应用或窗口";
  if (normalized.some((resource) => /network|web|url|browser/.test(resource))) return "网页或网络";
  if (normalized.some((resource) => /message|mail|chat/.test(resource))) return "消息内容";
  if (normalized.some((resource) => /system|process|shell/.test(resource))) return "系统资源";
  return resources.length ? resources.slice(0, 2).join(", ") : "未声明";
}

function buildDecisionSummary(
  approval: ApprovalRequest,
  plan: CleanupPlan | undefined,
  groups: ReturnType<typeof splitCleanupItems>
): ApprovalDecisionSummary {
  const totalItems = plan?.items.length ?? 0;
  const reclaimable = formatBytes(plan?.reclaimableBytes);
  const hasPermanent = groups.permanent.length > 0;
  const hasTrash = groups.trash.length > 0;
  const warningCount = plan?.riskWarnings.length ?? 0;
  const highRisk = approval.riskLevel === "high" || approval.riskLevel === "critical" || hasPermanent;

  if (!plan) {
    return {
      title: "批准前请确认动作内容",
      detail: "这不是自动执行。只有你点“批准”后，Lengrvis 才会继续这项操作。",
      scope: "查看动作说明",
      recovery: "按动作决定",
      guard: "等待你批准",
      tone: approval.riskLevel === "critical" || approval.riskLevel === "high" ? "danger" : "warning"
    };
  }

  return {
    title: highRisk ? "包含高影响清理项" : hasTrash ? "清理项将先进入回收站" : "本次只包含建议或低影响项",
    detail: `共 ${totalItems} 项，预计释放 ${reclaimable}。批准前不会移动或删除任何文件。`,
    scope: `${totalItems} 项 / ${reclaimable}`,
    recovery: hasPermanent ? "含不可恢复项" : hasTrash ? "可从回收站恢复" : "本次不直接删除",
    guard: warningCount ? `${warningCount} 条风险提示` : "等待你批准",
    tone: highRisk ? "danger" : warningCount ? "warning" : "safe"
  };
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])"
].join(",");

function focusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector))
    .filter((element) => !element.hasAttribute("disabled") && element.offsetParent !== null);
}

function firstFocusableElement(container: HTMLElement | null): HTMLElement | null {
  return focusableElements(container)[0] ?? null;
}

function trapFocus(event: KeyboardEvent, container: HTMLElement | null) {
  const focusables = focusableElements(container);
  if (!focusables.length) {
    event.preventDefault();
    container?.focus();
    return;
  }

  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function formatBytes(bytes?: number): string {
  if (!bytes || !Number.isFinite(bytes)) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function textValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(textValue).filter(Boolean);
}

function visibleToolSummary(value: unknown): string {
  const tools = stringList(value);
  if (!tools.length) return "";
  return tools.length <= 4 ? tools.join(", ") : `${tools.length} tools`;
}
