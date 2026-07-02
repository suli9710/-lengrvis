import { RotateCcw, ShieldCheck, Trash2 } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import type { CleanupPlan } from "../../../shared/types";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { zhUserFacingError } from "../../lib/zh";
import { Badge } from "../Panel";

type CleanupApprovalStatus = "idle" | "planning" | "ready" | "requesting" | "requested" | "error";

interface FileCleanupWorkspaceOptions {
  api?: LengrvisApiClient;
  currentScope: string;
  hasPendingApproval: boolean;
  onOpenApprovals?: () => void;
  onRequestCleanupApproval?: (scope: string) => Promise<void>;
}

interface FileCleanupWorkspaceModel {
  hasPreview: boolean;
  reset: () => void;
  pane: ReactNode;
}

export interface CleanupPreviewModel {
  permanent: CleanupPlan["items"];
  trash: CleanupPlan["items"];
  suggestions: CleanupPlan["items"];
  executableCount: number;
  needsApproval: boolean;
}

export function buildCleanupPreviewModel(plan: CleanupPlan | null): CleanupPreviewModel {
  const items = plan?.items ?? [];
  const permanent = items.filter((item) => item.disposition === "permanent_delete");
  const trash = items.filter((item) => item.disposition === "trash");
  const suggestions = items.filter(
    (item) => item.disposition !== "permanent_delete" && item.disposition !== "trash"
  );

  return {
    permanent,
    trash,
    suggestions,
    executableCount: permanent.length + trash.length,
    needsApproval: Boolean(plan && (
      plan.status === "needs_approval" || permanent.length > 0 || plan.riskWarnings.length > 0
    ))
  };
}

export function useFileCleanupWorkspace({
  api,
  currentScope,
  hasPendingApproval,
  onOpenApprovals,
  onRequestCleanupApproval
}: FileCleanupWorkspaceOptions): FileCleanupWorkspaceModel {
  const [plan, setPlan] = useState<CleanupPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approvalStatus, setApprovalStatus] = useState<CleanupApprovalStatus>("idle");
  const [approvalMessage, setApprovalMessage] = useState<string | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  const preview = useMemo(() => buildCleanupPreviewModel(plan), [plan]);

  const reset = useCallback(() => {
    setPlan(null);
    setError(null);
    setApprovalStatus("idle");
    setApprovalMessage(null);
  }, []);

  const scan = async () => {
    if (!api) return;
    if (!currentScope) {
      setError("请先选择要检查的文件夹范围。");
      return;
    }
    setIsWorking(true);
    setError(null);
    setApprovalStatus("idle");
    setApprovalMessage(null);
    try {
      const response = await api.scanCleanup({ roots: [currentScope], thresholdMb: 100 });
      if (response.ok && response.data) {
        setPlan(response.data);
      } else {
        setError(cleanupErrorText(response.error?.message, "暂时无法扫描可清理项，请稍后重试。"));
      }
    } catch (caughtError) {
      setError(cleanupErrorText(caughtError, "暂时无法扫描可清理项，请稍后重试。"));
    } finally {
      setIsWorking(false);
    }
  };

  const generateApprovalPreview = async () => {
    if (!api || isWorking) return;
    if (!currentScope) {
      setError("请先选择要检查的文件夹范围。");
      return;
    }
    setIsWorking(true);
    setError(null);
    setApprovalStatus("planning");
    setApprovalMessage("正在生成确认预览，只校验清单，不执行删除。");
    try {
      const response = await api.planCleanup({ roots: [currentScope], thresholdMb: 100, preferTrash: true });
      if (response.ok && response.data) {
        setPlan(response.data);
        const nextPreview = buildCleanupPreviewModel(response.data);
        setApprovalStatus("ready");
        setApprovalMessage(nextPreview.executableCount
          ? "确认预览已生成。下一步发起确认任务，Lengrvis 会等待你批准后才执行。"
          : "确认预览已生成，但当前只有建议项，没有可执行清理项。");
      } else {
        setApprovalStatus("error");
        setApprovalMessage(cleanupErrorText(response.error?.message, "确认预览生成失败，请稍后重试。"));
      }
    } catch (caughtError) {
      setApprovalStatus("error");
      setApprovalMessage(cleanupErrorText(caughtError, "确认预览生成失败，请稍后重试。"));
    } finally {
      setIsWorking(false);
    }
  };

  const requestApproval = async () => {
    if (!onRequestCleanupApproval || isWorking) return;
    if (!currentScope) {
      setError("请先选择要检查的文件夹范围。");
      return;
    }
    setApprovalStatus("requesting");
    setApprovalMessage("正在发起确认任务；在你批准前不会移动或删除文件。");
    try {
      await onRequestCleanupApproval(currentScope);
      setApprovalStatus("requested");
      setApprovalMessage("确认任务已发起。审批出现后，点“去确认”查看清单并决定批准或拒绝。");
    } catch (caughtError) {
      setApprovalStatus("error");
      setApprovalMessage(cleanupErrorText(caughtError, "确认任务发起失败，请稍后重试。"));
    }
  };

  return {
    hasPreview: Boolean(plan),
    reset,
    pane: (
      <FileCleanupPane
        plan={plan}
        preview={preview}
        error={error}
        approvalStatus={approvalStatus}
        approvalMessage={approvalMessage}
        isWorking={isWorking}
        hasPendingApproval={hasPendingApproval}
        canRequestApproval={Boolean(onRequestCleanupApproval)}
        canOpenApprovals={Boolean(onOpenApprovals)}
        onScan={() => void scan()}
        onReset={reset}
        onGenerateApprovalPreview={() => void generateApprovalPreview()}
        onRequestApproval={() => void requestApproval()}
        onOpenApprovals={onOpenApprovals}
      />
    )
  };
}

interface FileCleanupPaneProps {
  plan: CleanupPlan | null;
  preview: CleanupPreviewModel;
  error: string | null;
  approvalStatus: CleanupApprovalStatus;
  approvalMessage: string | null;
  isWorking: boolean;
  hasPendingApproval: boolean;
  canRequestApproval: boolean;
  canOpenApprovals: boolean;
  onScan: () => void;
  onReset: () => void;
  onGenerateApprovalPreview: () => void;
  onRequestApproval: () => void;
  onOpenApprovals?: () => void;
}

function FileCleanupPane({
  plan,
  preview,
  error,
  approvalStatus,
  approvalMessage,
  isWorking,
  hasPendingApproval,
  canRequestApproval,
  canOpenApprovals,
  onScan,
  onReset,
  onGenerateApprovalPreview,
  onRequestApproval,
  onOpenApprovals
}: FileCleanupPaneProps) {
  return (
    <section className="file-tool-pane" aria-label="清理预览">
      <div className="file-tool">
        <div className="file-tool__head">
          <div>
            <strong>清理预览</strong>
            <span className="muted">只扫描当前范围，扫描后再决定，不会直接删除</span>
          </div>
          <Badge tone={preview.permanent.length ? "warning" : "neutral"}>
            {formatBytes(plan?.reclaimableBytes)} 可释放
          </Badge>
        </div>
        {!plan ? (
          <div className="cleanup-safety-gate">
            <div>
              <strong>先扫描，不执行</strong>
              <p>这一步只读取文件信息，不移动、不删除。生成预览后，你再决定是否继续。</p>
            </div>
            <span>只读</span>
          </div>
        ) : null}
        <button className="button button--secondary" type="button" onClick={onScan} disabled={isWorking}>
          <Trash2 size={16} aria-hidden="true" />
          {isWorking && approvalStatus === "idle" ? "正在扫描" : "只读扫描可清理项"}
        </button>
        {error ? <p className="field-error">{error}</p> : null}
        {plan ? (
          <>
            <div className="cleanup-action-row" aria-label="清理确认动作">
              <button className="button button--ghost" type="button" onClick={onReset} disabled={isWorking}>
                <RotateCcw size={16} aria-hidden="true" />
                放弃本次预览
              </button>
              <button
                className="button button--secondary"
                type="button"
                onClick={onGenerateApprovalPreview}
                disabled={isWorking}
              >
                <ShieldCheck size={16} aria-hidden="true" />
                {approvalStatus === "planning" ? "正在生成确认预览" : "生成确认预览"}
              </button>
              {canRequestApproval ? (
                <button
                  className="button button--primary"
                  type="button"
                  onClick={onRequestApproval}
                  disabled={isWorking || preview.executableCount === 0}
                  title={preview.executableCount === 0 ? "当前没有可执行清理项" : "发起一个需要你批准的清理任务"}
                >
                  <ShieldCheck size={16} aria-hidden="true" />
                  {approvalStatus === "requesting" ? "正在发起确认任务" : "发起确认任务"}
                </button>
              ) : null}
              {hasPendingApproval && canOpenApprovals ? (
                <button className="button button--ghost" type="button" onClick={onOpenApprovals}>
                  去确认
                </button>
              ) : null}
            </div>
            {approvalMessage ? (
              <p
                className={`file-status file-status--${approvalStatus === "error" ? "error" : approvalStatus === "ready" || approvalStatus === "requested" ? "success" : "info"}`}
                role={approvalStatus === "error" ? "alert" : "status"}
              >
                {approvalMessage}
              </p>
            ) : null}
            <CleanupPlanPreview plan={plan} preview={preview} />
          </>
        ) : (
          <p className="file-status file-status--info">清理工具只会先给预览。真正移动或删除文件前，还会让你确认。</p>
        )}
      </div>
    </section>
  );
}

function CleanupPlanPreview({ plan, preview }: { plan: CleanupPlan; preview: CleanupPreviewModel }) {
  return (
    <div className="cleanup-preview">
      <div className={preview.needsApproval ? "cleanup-safety-gate cleanup-safety-gate--approval" : "cleanup-safety-gate"}>
        <div>
          <strong>{preview.needsApproval ? "等待你确认后才会执行" : "当前只是安全预览"}</strong>
          <p>
            {preview.needsApproval
              ? "包含永久删除或风险项。Lengrvis 会先生成审批预览，确认后才允许执行。"
              : "扫描不会移动或删除文件；你可以先看清单，再决定下一步。"}
          </p>
        </div>
        <span>{preview.needsApproval ? "需确认" : "只读"}</span>
      </div>
      <div className="cleanup-preview__metrics">
        <span><strong>{formatBytes(plan.reclaimableBytes)}</strong> 可释放</span>
        <span><strong>{preview.permanent.length}</strong> 永久删除</span>
        <span><strong>{preview.trash.length}</strong> 进回收站</span>
      </div>
      <div className="cleanup-approval-steps" aria-label="清理安全步骤">
        <span className="cleanup-approval-step cleanup-approval-step--done">1 只读扫描</span>
        <span className={plan.items.length ? "cleanup-approval-step cleanup-approval-step--done" : "cleanup-approval-step"}>2 风险分桶</span>
        <span className={preview.needsApproval ? "cleanup-approval-step cleanup-approval-step--current" : "cleanup-approval-step"}>3 用户确认</span>
        <span className="cleanup-approval-step">4 执行或放弃</span>
      </div>
      <CleanupBucket title="永久删除" tone="danger" items={preview.permanent} emptyText="没有永久删除项" />
      <CleanupBucket title="进回收站" tone="warning" items={preview.trash} emptyText="没有回收站项" />
      <CleanupBucket title="仅建议" description="仅供你查看，Lengrvis 不会删除这些项目。" tone="neutral" items={preview.suggestions} emptyText="没有建议项" />
      {plan.riskWarnings.length ? (
        <ul className="cleanup-risk">
          {plan.riskWarnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function CleanupBucket({
  title,
  description,
  tone,
  items,
  emptyText
}: {
  title: string;
  description?: string;
  tone: "neutral" | "warning" | "danger";
  items: CleanupPlan["items"];
  emptyText: string;
}) {
  return (
    <section className="cleanup-bucket">
      <div className="row row--between">
        <strong>{title}</strong>
        <Badge tone={tone}>{items.length} 项</Badge>
      </div>
      {description ? <p className="muted">{description}</p> : null}
      {items.length ? (
        <ul>
          {items.slice(0, 5).map((item) => (
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

function cleanupErrorText(error: unknown, fallback: string): string {
  const raw = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return zhUserFacingError(raw) || fallback;
}
