import type { ApprovalRequest, SafetyReview } from "../../../shared/executionTypes";
import { zhApprovalType, zhBackendText } from "../zh";
import type { BackendApproval } from "./executionBackendTypes";
import { cleanupPlanFromApprovalPayload } from "./cleanupMappers";
import { optionalObjectRecord } from "./mapperPrimitives";

export function mapRiskSeverity(risk: string): SafetyReview["findings"][number]["severity"] {
  if (risk.startsWith("R4")) return "critical";
  if (risk.startsWith("R3")) return "high";
  if (risk.startsWith("R2")) return "medium";
  return "low";
}

export function mapApproval(approval: BackendApproval): ApprovalRequest {
  const cleanupPlan = cleanupPlanFromApprovalPayload(approval.diff_preview);
  return {
    id: approval.id,
    taskId: approval.task_id ? String(approval.task_id) : undefined,
    stepId: approval.step_id === undefined ? undefined : approval.step_id,
    approvalType: approval.approval_type,
    title: cleanupPlan ? "清理计划审批" : zhApprovalType(approval.approval_type),
    reason: zhBackendText(approval.message),
    requester: "HumanGateAgent",
    riskLevel: cleanupPlan?.items.some((item) => item.disposition === "permanent_delete")
      ? "high"
      : mapRiskSeverity(approval.risk_level ?? ""),
    createdAt: approval.created_at,
    proposedAction: formatDiffPreview(approval.diff_preview),
    status: approval.status === "rejected" ? "denied" : approval.status === "approved" ? "approved" : "pending",
    rawPayload: approval.diff_preview,
    cleanupPlan,
    toolName: approval.tool_name,
    toolTrustTier: approval.tool_trust_tier,
    toolEffects: approval.tool_effects ?? [],
    resourceKinds: approval.resource_kinds ?? [],
    policyMode: approval.policy_mode ?? approval.permission_mode,
    dryRunSummary: approval.dry_run_summary,
    modelAction: optionalObjectRecord(approval.model_action),
    runtimeControlFields: optionalObjectRecord(approval.runtime_control_fields ?? approval.runtime_fields),
    engineeringBoundary: optionalObjectRecord(approval.engineering_boundary)
  };
}

export function formatDiffPreview(diffPreview: unknown): string {
  if (!diffPreview || typeof diffPreview !== "object") {
    return String(diffPreview ?? "无预览内容");
  }
  return JSON.stringify(localizeDiffPreview(diffPreview), null, 2);
}

export function localizeDiffPreview(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(localizeDiffPreview);
  }
  if (!value || typeof value !== "object") {
    if (typeof value === "string") {
      return zhBackendText(value);
    }
    return value;
  }
  const labels: Record<string, string> = {
    dry_run: "试运行",
    operation: "操作",
    query: "查询",
    diff_preview: "变更预览",
    message: "说明",
    action: "动作",
    from: "来源",
    to: "目标",
    path: "路径",
    bytes: "字节数",
    would_create: "将创建",
    changed_paths: "变更路径",
    rollback_info: "回滚信息",
    error: "错误"
  };
  const actions: Record<string, string> = {
    preview: "预览",
    copy: "复制",
    move: "移动",
    rename: "重命名",
    trash: "移入回收站",
    write_text: "写入文本",
    generate_markdown_report: "生成 Markdown 报告",
    organize_files: "整理文件"
  };
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      const translatedKey = labels[key] ?? key;
      const translatedValue = typeof item === "string" && key === "action" ? actions[item] ?? item : localizeDiffPreview(item);
      return [translatedKey, translatedValue];
    })
  );
}
