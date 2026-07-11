export type ApprovalDecisionGuardTone = "safe" | "warning" | "danger";

export interface ApprovalDecisionGuardCopy {
  title: string;
  detail: string;
  nextStep: string;
  tone: ApprovalDecisionGuardTone;
  approveBlockedReason?: string;
}

export interface ApprovalListSafetyCopy {
  label: string;
  detail: string;
  tone: ApprovalDecisionGuardTone;
  approveBlockedReason?: string;
}

export interface ApprovalActiveGrantContext {
  deviceId?: string;
  grantId?: string;
  bindingRef?: string;
}

interface ApprovalSafetyInput {
  approval_type?: string;
  diff_preview?: unknown;
  dry_run_summary?: string;
  engineering_boundary?: unknown;
  permission_mode?: string;
  policy_mode?: string;
  preview?: unknown;
  resource_kinds?: string[];
  risk_level?: string;
  source?: string;
  source_device_id?: string;
  source_grant_id?: string;
  allowed_device_ids?: string[];
  tool_effects?: string[];
  tool_name?: string;
  tool_trust_tier?: string;
  required_mobile_scopes?: string[];
  mobile_step_up_required?: boolean;
  mobile_step_up_satisfied?: boolean;
  remote_input_binding?: {
    device_bound?: boolean;
    grant_bound?: boolean;
    requires_remote_input_scope?: boolean;
    binding_ref?: string;
    matches_current_device?: boolean;
    matches_current_grant?: boolean;
  };
}

const FORBIDDEN_APPROVAL_REASON = "这类请求不能在手机上批准；请回电脑端处理或直接拒绝。";
const UNVERIFIED_HIGH_RISK_REASON = "此审批缺少可核对的试运行或影响范围，手机端不会批准；请回电脑端查看后处理。";
const REMOTE_INPUT_BINDING_REASON = "远控输入审批必须绑定当前远控授权，手机端不会批准缺少授权绑定的请求。";
const REMOTE_INPUT_ACTIVE_GRANT_REASON = "此审批没有匹配当前手机的远控授权，手机端不会批准；请回电脑端重新发起授权。";
const REMOTE_INPUT_DESKTOP_APPROVAL_REASON = "远控输入审批不能在手机端批准；请回电脑端核对并处理，或直接拒绝。";
const DANGEROUS_PERMISSION_REASON = "此审批会扩大电脑端执行权限，不能在手机上批准；请回电脑端核对后手动处理或拒绝。";
const BIOMETRIC_STEP_UP_REASON = "此操作影响较高，当前版本没有可信的硬件绑定生物识别在场证明；请回电脑端处理或拒绝。";

export function approvalApproveBlockedReason(approval: ApprovalSafetyInput, activeGrant?: ApprovalActiveGrantContext | null): string | null {
  if (isForbiddenOrHandoff(approval) || isDesktopOnlyApproval(approval)) {
    return FORBIDDEN_APPROVAL_REASON;
  }
  if (isRemoteInputApproval(approval)) {
    if (!remoteInputApprovalHasBinding(approval)) {
      return REMOTE_INPUT_BINDING_REASON;
    }
    if (!remoteInputApprovalMatchesActiveGrant(approval, activeGrant)) {
      return REMOTE_INPUT_ACTIVE_GRANT_REASON;
    }
    return REMOTE_INPUT_DESKTOP_APPROVAL_REASON;
  }
  if (hasDangerousPermissionMode(approval)) {
    return DANGEROUS_PERMISSION_REASON;
  }
  if (requiresTrustedMobileStepUp(approval)) {
    return BIOMETRIC_STEP_UP_REASON;
  }
  if (isHighRiskApproval(approval) && (!hasDryRunSummary(approval) || !hasDeclaredScope(approval))) {
    return UNVERIFIED_HIGH_RISK_REASON;
  }
  return null;
}

export function remoteInputMobileDecisionBlockedReason(approval: ApprovalSafetyInput, activeGrant?: ApprovalActiveGrantContext | null): string | null {
  if (!isRemoteInputApproval(approval)) return null;
  const reason = approvalApproveBlockedReason(approval, activeGrant);
  return reason === REMOTE_INPUT_BINDING_REASON ||
    reason === REMOTE_INPUT_ACTIVE_GRANT_REASON ||
    reason === REMOTE_INPUT_DESKTOP_APPROVAL_REASON
    ? reason
    : null;
}

export function approvalDecisionGuard(approval: ApprovalSafetyInput, activeGrant?: ApprovalActiveGrantContext | null): ApprovalDecisionGuardCopy {
  const approveBlockedReason = approvalApproveBlockedReason(approval, activeGrant);
  if (approveBlockedReason) {
    if (approveBlockedReason === UNVERIFIED_HIGH_RISK_REASON) {
      return {
        title: "缺少可核对的安全边界",
        detail: "未看到完整的安全试运行摘要或影响范围，默认更安全的下一步是拒绝。",
        nextStep: "点拒绝，或回电脑端查看试运行、对象和恢复方式后再处理。",
        tone: "danger",
        approveBlockedReason,
      };
    }
    if (approveBlockedReason === REMOTE_INPUT_BINDING_REASON) {
      return {
        title: "远控授权不完整",
        detail: "此请求没有绑定当前手机的远控授权。不要用普通审批绕过远控输入保护。",
        nextStep: "回到电脑端或远控页面重新发起授权；不确定就在电脑端拒绝。",
        tone: "danger",
        approveBlockedReason,
      };
    }
    if (approveBlockedReason === REMOTE_INPUT_ACTIVE_GRANT_REASON) {
      return {
        title: "远控授权不匹配",
        detail: "这项审批没有匹配当前手机正在使用的远控授权。不要用旧授权或其他授权批准。",
        nextStep: "回到电脑端重新发起远控授权；不确定就在电脑端拒绝。",
        tone: "danger",
        approveBlockedReason,
      };
    }
    if (approveBlockedReason === REMOTE_INPUT_DESKTOP_APPROVAL_REASON) {
      return {
        title: "远控输入需电脑端确认",
        detail: "手机端只能发起或拒绝这类远控输入请求，不能用同一远控授权批准自己触发的动作。",
        nextStep: "回电脑端核对光标、窗口和动作后处理；不确定就在手机端拒绝。",
        tone: "danger",
        approveBlockedReason,
      };
    }
    if (approveBlockedReason === DANGEROUS_PERMISSION_REASON) {
      return {
        title: "手机端不会扩大电脑权限",
        detail: "此请求涉及更高执行权限或绕过常规确认。手机屏幕不适合核对完整影响范围。",
        nextStep: "点拒绝，或回电脑端查看权限、对象和恢复方式后手动处理。",
        tone: "danger",
        approveBlockedReason,
      };
    }
    if (approveBlockedReason === BIOMETRIC_STEP_UP_REASON) {
      return {
        title: "需要生物识别再次确认",
        detail: "当前版本尚未向后端提供可验证的生物识别 step-up，因此不会在手机端批准高影响发送或提交。",
        nextStep: "回电脑端核对并处理；不确定就直接拒绝。",
        tone: "danger",
        approveBlockedReason,
      };
    }
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

export function approvalListSafety(approval: ApprovalSafetyInput, activeGrant?: ApprovalActiveGrantContext | null): ApprovalListSafetyCopy {
  const guard = approvalDecisionGuard(approval, activeGrant);
  if (guard.approveBlockedReason) {
    if (guard.approveBlockedReason === UNVERIFIED_HIGH_RISK_REASON) {
      return {
        label: "缺少安全边界",
        detail: "手机端不可批准；回电脑端核对试运行和影响范围。",
        tone: "danger",
        approveBlockedReason: guard.approveBlockedReason,
      };
    }
    if (guard.approveBlockedReason === REMOTE_INPUT_BINDING_REASON) {
      return {
        label: "远控授权不完整",
        detail: "手机端不可批准；重新发起远控授权或在电脑端拒绝。",
        tone: "danger",
        approveBlockedReason: guard.approveBlockedReason,
      };
    }
    if (guard.approveBlockedReason === REMOTE_INPUT_ACTIVE_GRANT_REASON) {
      return {
        label: "远控授权不匹配",
        detail: "手机端不可批准；重新发起远控授权或在电脑端拒绝。",
        tone: "danger",
        approveBlockedReason: guard.approveBlockedReason,
      };
    }
    if (guard.approveBlockedReason === REMOTE_INPUT_DESKTOP_APPROVAL_REASON) {
      return {
        label: "远控需电脑确认",
        detail: "手机端不可批准；回电脑端核对动作后处理，或直接拒绝。",
        tone: "danger",
        approveBlockedReason: guard.approveBlockedReason,
      };
    }
    if (guard.approveBlockedReason === BIOMETRIC_STEP_UP_REASON) {
      return {
        label: "需要生物识别",
        detail: "当前手机会话不能批准高影响操作；请回电脑端处理。",
        tone: "danger",
        approveBlockedReason: guard.approveBlockedReason,
      };
    }
    return {
      label: "手机不可批准",
      detail: "此类请求需要回电脑端处理，或直接拒绝。",
      tone: "danger",
      approveBlockedReason: guard.approveBlockedReason,
    };
  }
  if (guard.tone === "danger") {
    return {
      label: "高风险待核对",
      detail: guard.nextStep,
      tone: "danger",
    };
  }
  if (guard.tone === "warning") {
    return {
      label: "批准前核对",
      detail: guard.nextStep,
      tone: "warning",
    };
  }
  return {
    label: "可在手机核对",
    detail: guard.nextStep,
    tone: "safe",
  };
}

function isHighRiskApproval(approval: ApprovalSafetyInput): boolean {
  const risk = normalizedRiskText(approval);
  if (risk.includes("r3") || risk.includes("danger") || risk.includes("destructive") || risk.includes("system") || risk.includes("critical") || risk.includes("high")) {
    return true;
  }
  const effects = stringList(approval.tool_effects).join(" ").toLowerCase();
  const action = `${approval.approval_type || ""} ${approval.tool_name || ""}`.toLowerCase();
  return /delete|remove|clean|trash|permanent|write|modify|move|input|click|type|system|shell|execute|run|launch|install|uninstall|sudo|admin|privileged|registry|firewall|credential|keychain/.test(`${effects} ${action}`);
}

function requiresTrustedMobileStepUp(approval: ApprovalSafetyInput): boolean {
  if (approval.mobile_step_up_required === true) return true;
  const boundary = objectValue(approval.engineering_boundary);
  const tool = objectValue(boundary.tool);
  const risk = normalizedRiskText(approval);
  if (/r3|destructive|system|critical/.test(risk) || booleanValue(tool.destructive)) return true;
  if (hasDangerousPermissionMode(approval)) return true;
  const effects = [...stringList(approval.tool_effects), ...stringList(tool.effects)].join(" ").toLowerCase();
  const action = `${approval.approval_type || ""} ${approval.tool_name || ""}`.toLowerCase();
  return /credential|delete|destructive|execute|external[_ -]?post|install|payment|permission|privileged|process|purchase|registry|send|submit|system[_ -]?write|trash|uninstall|upload/.test(`${effects} ${action}`);
}

function hasDryRunSummary(approval: ApprovalSafetyInput): boolean {
  const boundary = objectValue(approval.engineering_boundary);
  const dryRun = objectValue(boundary.dry_run);
  return Boolean(
    textValue(approval.dry_run_summary).trim() ||
      textValue(dryRun.summary).trim() ||
      hasInspectablePreview(approval.diff_preview) ||
      hasInspectablePreview(approval.preview),
  );
}

function hasDeclaredScope(approval: ApprovalSafetyInput): boolean {
  const boundary = objectValue(approval.engineering_boundary);
  const tool = objectValue(boundary.tool);
  if (
    isRemoteInputApproval(approval) &&
    stringList(approval.required_mobile_scopes).some((scope) => scope.toLowerCase() === "remote:input")
  ) {
    return true;
  }
  return stringList(approval.resource_kinds).length > 0 || stringList(tool.resource_kinds).length > 0;
}

function normalizedRiskText(approval: ApprovalSafetyInput): string {
  const boundary = objectValue(approval.engineering_boundary);
  const tool = objectValue(boundary.tool);
  return `${approval.risk_level || ""} ${textValue(tool.risk_level)}`.toLowerCase();
}

function isForbiddenOrHandoff(approval: ApprovalSafetyInput): boolean {
  const risk = normalizedRiskText(approval);
  const effects = stringList(approval.tool_effects).join(" ").toLowerCase();
  const boundary = objectValue(approval.engineering_boundary);
  const policy = objectValue(boundary.policy);
  const policyText = `${textValue(boundary.policy_reason)} ${textValue(policy.reason)} ${textValue(policy.verdict)}`.toLowerCase();
  return (
    risk.includes("r4") ||
    risk.includes("forbidden") ||
    risk.includes("handoff") ||
    effects.includes("forbidden") ||
    effects.includes("handoff") ||
    policyText.includes("forbidden") ||
    policyText.includes("handoff")
  );
}

function isDesktopOnlyApproval(approval: ApprovalSafetyInput): boolean {
  const modeText = `${approval.permission_mode || ""} ${approval.policy_mode || ""}`.toLowerCase();
  return /desktop[_ -]?only|local[_ -]?only|manual[_ -]?handoff/.test(modeText);
}

function hasDangerousPermissionMode(approval: ApprovalSafetyInput): boolean {
  const boundary = objectValue(approval.engineering_boundary);
  const policy = objectValue(boundary.policy);
  const modeText = [
    approval.permission_mode,
    approval.policy_mode,
    textValue(boundary.permission_mode),
    textValue(boundary.policy_mode),
    textValue(policy.permission_mode),
    textValue(policy.mode),
  ].join(" ").toLowerCase();
  return /danger[-_ ]?full[-_ ]?access|full[-_ ]?access|unrestricted|bypass|override|no[-_ ]?approval|auto[-_ ]?approve|admin|root|privileged/.test(modeText);
}

function isRemoteInputApproval(approval: ApprovalSafetyInput): boolean {
  return (
    `${approval.approval_type || ""} ${approval.source || ""}`.toLowerCase().includes("remote_input") ||
    stringList(approval.required_mobile_scopes).some((scope) => scope.toLowerCase() === "remote:input")
  );
}

function remoteInputApprovalMatchesActiveGrant(
  approval: ApprovalSafetyInput,
  activeGrant?: ApprovalActiveGrantContext | null,
): boolean {
  if (!activeGrant?.deviceId || !activeGrant?.grantId) return false;
  const sourceDeviceId = textValue(approval.source_device_id).trim();
  const sourceGrantId = textValue(approval.source_grant_id).trim();
  const binding = objectValue(approval.remote_input_binding);
  const allowedDevices = stringList(approval.allowed_device_ids).map((deviceId) => deviceId.trim()).filter(Boolean);
  if (allowedDevices.length > 0 && !allowedDevices.includes(activeGrant.deviceId)) return false;
  if (sourceDeviceId && sourceDeviceId !== activeGrant.deviceId) return false;
  if (sourceGrantId && sourceGrantId !== activeGrant.grantId) return false;
  if (booleanValue(binding.matches_current_device) === false && binding.matches_current_device !== undefined) return false;
  const bindingRef = textValue(binding.binding_ref).trim();
  if (!sourceGrantId) {
    if (bindingRef) {
      if (!activeGrant.bindingRef || activeGrant.bindingRef !== bindingRef) return false;
    } else if (binding.matches_current_grant !== undefined) {
      if (!booleanValue(binding.matches_current_grant)) return false;
    } else {
      return false;
    }
  }
  if (booleanValue(binding.matches_current_grant) === false && binding.matches_current_grant !== undefined) return false;
  if (sourceDeviceId && sourceGrantId) return true;
  return remoteInputApprovalHasPublicBinding(approval);
}

function remoteInputApprovalHasBinding(approval: ApprovalSafetyInput): boolean {
  const sourceDeviceId = textValue(approval.source_device_id).trim();
  const sourceGrantId = textValue(approval.source_grant_id).trim();
  if (sourceDeviceId && sourceGrantId) return true;
  return remoteInputApprovalHasPublicBinding(approval);
}

function remoteInputApprovalHasPublicBinding(approval: ApprovalSafetyInput): boolean {
  const binding = objectValue(approval.remote_input_binding);
  return (
    booleanValue(binding.device_bound) &&
    booleanValue(binding.grant_bound) &&
    booleanValue(binding.requires_remote_input_scope)
  );
}

function hasInspectablePreview(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasInspectablePreview);
  const object = objectValue(value);
  if (Object.keys(object).length === 0) return Boolean(textValue(value).trim());
  const diffPreview = object.diff_preview;
  if (Array.isArray(diffPreview)) return diffPreview.some(hasInspectablePreview);
  return Object.entries(object).some(([key, item]) => !key.startsWith("_") && hasInspectablePreview(item));
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

function booleanValue(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") return value.toLowerCase() === "true";
  return false;
}
