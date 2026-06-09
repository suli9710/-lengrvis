export type ApprovalDecisionGuardTone = "safe" | "warning" | "danger";

export interface ApprovalDecisionGuardCopy {
  title: string;
  detail: string;
  nextStep: string;
  tone: ApprovalDecisionGuardTone;
  approveBlockedReason?: string;
}

interface ApprovalSafetyInput {
  approval_type?: string;
  dry_run_summary?: string;
  engineering_boundary?: unknown;
  resource_kinds?: string[];
  risk_level?: string;
  tool_effects?: string[];
  tool_name?: string;
}

const FORBIDDEN_APPROVAL_REASON = "这类请求不能在手机上批准；请回电脑端处理或直接拒绝。";

export function approvalApproveBlockedReason(approval: ApprovalSafetyInput): string | null {
  const risk = normalizedRiskText(approval);
  if (risk.includes("r4") || risk.includes("forbidden") || risk.includes("handoff")) {
    return FORBIDDEN_APPROVAL_REASON;
  }
  return null;
}

export function approvalDecisionGuard(approval: ApprovalSafetyInput): ApprovalDecisionGuardCopy {
  const approveBlockedReason = approvalApproveBlockedReason(approval);
  if (approveBlockedReason) {
    return {
      title: "手机端不会批准此类请求",
      detail: "它被标记为禁止或需要人工接管。不要尝试绕过审批边界。",
      nextStep: "点拒绝，或回电脑端查看原因后手动处理。",
      tone: "danger",
      approveBlockedReason,
    };
  }

  const highRisk = isHighRiskApproval(approval);
  const hasDryRun = hasDryRunSummary(approval);
  const hasScope = hasDeclaredScope(approval);
  if (highRisk) {
    return {
      title: "批准前先核对范围",
      detail: hasDryRun
        ? "系统已停在审批点；批准后电脑端才会继续执行这一步。"
        : "未看到安全试运行摘要，默认更安全的下一步是拒绝。",
      nextStep: hasScope
        ? "只在动作、对象和恢复方式都看懂时批准；不确定就拒绝。"
        : "先回电脑端确认影响范围；看不懂就拒绝。",
      tone: hasDryRun && hasScope ? "warning" : "danger",
    };
  }

  return {
    title: "先确认再批准",
    detail: "批准后电脑端才会继续这一步；现在仍停在等待你决定的状态。",
    nextStep: "看不懂动作、对象或后果时，点拒绝是更安全的选择。",
    tone: hasDryRun ? "safe" : "warning",
  };
}

function isHighRiskApproval(approval: ApprovalSafetyInput): boolean {
  const risk = normalizedRiskText(approval);
  if (risk.includes("r3") || risk.includes("destructive") || risk.includes("system") || risk.includes("critical") || risk.includes("high")) {
    return true;
  }
  const effects = stringList(approval.tool_effects).join(" ").toLowerCase();
  const action = `${approval.approval_type || ""} ${approval.tool_name || ""}`.toLowerCase();
  return /delete|remove|clean|trash|write|modify|move|input|click|type|system|shell|execute|launch/.test(`${effects} ${action}`);
}

function hasDryRunSummary(approval: ApprovalSafetyInput): boolean {
  const boundary = objectValue(approval.engineering_boundary);
  const dryRun = objectValue(boundary.dry_run);
  return Boolean(textValue(approval.dry_run_summary).trim() || textValue(dryRun.summary).trim());
}

function hasDeclaredScope(approval: ApprovalSafetyInput): boolean {
  const boundary = objectValue(approval.engineering_boundary);
  const tool = objectValue(boundary.tool);
  return stringList(approval.resource_kinds).length > 0 || stringList(tool.resource_kinds).length > 0;
}

function normalizedRiskText(approval: ApprovalSafetyInput): string {
  const boundary = objectValue(approval.engineering_boundary);
  const tool = objectValue(boundary.tool);
  return `${approval.risk_level || ""} ${textValue(tool.risk_level)}`.toLowerCase();
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(textValue).filter(Boolean);
}

function textValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}
