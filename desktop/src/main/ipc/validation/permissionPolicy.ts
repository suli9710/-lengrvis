import { API_REQUEST_SECURITY_LIMITS } from "../../../shared/ipc";
import type {
  DesktopPermissionPolicyRelaxationRequest,
  DesktopPermissionRule,
  DesktopPermissionRuleDeleteRequest,
  DesktopPermissionRuleUpsertRequest
} from "../../../shared/types";
import {
  ApiRequestValidationError,
  assertSafeFieldName,
  validateBridgeBoolean,
  validateBridgeEnum,
  validateBridgeIdentifier,
  validateBridgeStringArray,
  validateBridgeStringValue,
  validateOptionalConfirmationNonce,
  validatePlainBridgeBody
} from "./primitives";

const PERMISSION_EFFECTS = new Set(["allow", "deny"]);
const PERMISSION_RELAXATION_ACTIONS = new Set(["upsert_rule", "delete_rule", "replace_policy"]);
const PERMISSION_RULE_ALLOWED_KEYS = new Set([
  "id",
  "name",
  "effect",
  "tool",
  "tools",
  "path_pattern",
  "path_patterns",
  "time_window",
  "time_windows",
  "enabled",
  "reason"
]);
const PERMISSION_TIME_WINDOW_ALLOWED_KEYS = new Set(["days", "start", "end", "timezone"]);

export function validatePermissionPolicyRelaxationRequest(value: unknown): Record<string, unknown> {
  const request = validatePlainBridgeBody(value, "permission policy confirmation request");
  const action = validateBridgeEnum<DesktopPermissionPolicyRelaxationRequest["action"]>(
    request.action,
    "permission policy action",
    PERMISSION_RELAXATION_ACTIONS
  );
  if (action === "upsert_rule") {
    return { action, rule: validatePermissionRule(request.rule) };
  }
  if (action === "delete_rule") {
    return {
      action,
      rule_id: validateBridgeIdentifier(request.ruleId ?? request.rule_id, "permission rule id")
    };
  }
  return { action, policy: validatePermissionPolicy(request.policy) };
}

export function validatePermissionRuleUpsertRequest(value: unknown): DesktopPermissionRuleUpsertRequest {
  const request = validatePlainBridgeBody(value, "permission rule upsert request");
  return {
    rule: validatePermissionRule(request.rule),
    confirmationNonce: validateOptionalConfirmationNonce(request.confirmationNonce ?? request.confirmation_nonce)
  };
}

export function validatePermissionRuleDeleteRequest(value: unknown): DesktopPermissionRuleDeleteRequest {
  const request = validatePlainBridgeBody(value, "permission rule delete request");
  return {
    ruleId: validateBridgeIdentifier(request.ruleId ?? request.rule_id, "permission rule id"),
    confirmationNonce: validateOptionalConfirmationNonce(request.confirmationNonce ?? request.confirmation_nonce)
  };
}

export function validatePermissionPolicy(value: unknown): { rules?: DesktopPermissionRule[] } {
  const request = validatePlainBridgeBody(value, "permission policy");
  for (const key of Object.keys(request)) {
    if (key !== "rules") {
      throw new ApiRequestValidationError(`permission policy field is not allowed: ${key}`);
    }
  }
  if (request.rules === undefined) {
    return {};
  }
  if (!Array.isArray(request.rules) || request.rules.length > 200) {
    throw new ApiRequestValidationError("permission policy rules are invalid");
  }
  return { rules: request.rules.map((rule) => validatePermissionRule(rule)) };
}

function validatePermissionRule(value: unknown): DesktopPermissionRule {
  const request = validatePlainBridgeBody(value, "permission rule");
  for (const key of Object.keys(request)) {
    assertSafeFieldName(key, "permission rule key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (!PERMISSION_RULE_ALLOWED_KEYS.has(key)) {
      throw new ApiRequestValidationError(`permission rule field is not allowed: ${key}`);
    }
  }

  const rule: DesktopPermissionRule = {};
  if (request.id !== undefined) {
    rule.id = validateBridgeIdentifier(request.id, "permission rule id");
  }
  if (request.name !== undefined) {
    rule.name = validateBridgeStringValue(request.name, "permission rule name", 256, { allowEmpty: true, trim: true });
  }
  if (request.effect !== undefined) {
    rule.effect = validateBridgeEnum<"allow" | "deny">(request.effect, "permission rule effect", PERMISSION_EFFECTS);
  }
  if (request.tool !== undefined) {
    rule.tool = validateBridgeStringValue(request.tool, "permission rule tool", 256, { allowEmpty: true, trim: true });
  }
  if (request.tools !== undefined) {
    rule.tools = validateBridgeStringArray(request.tools, "permission rule tools", 100, 256);
  }
  if (request.path_pattern !== undefined) {
    rule.path_pattern = validateBridgeStringValue(request.path_pattern, "permission rule path pattern", 4096, {
      allowEmpty: true,
      trim: true
    });
  }
  if (request.path_patterns !== undefined) {
    rule.path_patterns = validateBridgeStringArray(request.path_patterns, "permission rule path patterns", 200, 4096);
  }
  if (request.time_window !== undefined) {
    rule.time_window = request.time_window === null ? null : validatePermissionTimeWindow(request.time_window);
  }
  if (request.time_windows !== undefined) {
    if (!Array.isArray(request.time_windows) || request.time_windows.length > 50) {
      throw new ApiRequestValidationError("permission rule time windows are invalid");
    }
    rule.time_windows = request.time_windows.map((window) => validatePermissionTimeWindow(window));
  }
  if (request.enabled !== undefined) {
    rule.enabled = validateBridgeBoolean(request.enabled, "permission rule enabled");
  }
  if (request.reason !== undefined) {
    rule.reason = validateBridgeStringValue(request.reason, "permission rule reason", 2048, {
      allowEmpty: true,
      trim: true
    });
  }
  return rule;
}

function validatePermissionTimeWindow(value: unknown): NonNullable<DesktopPermissionRule["time_window"]> {
  const request = validatePlainBridgeBody(value, "permission time window");
  for (const key of Object.keys(request)) {
    assertSafeFieldName(key, "permission time window key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (!PERMISSION_TIME_WINDOW_ALLOWED_KEYS.has(key)) {
      throw new ApiRequestValidationError(`permission time window field is not allowed: ${key}`);
    }
  }

  const timeWindow: NonNullable<DesktopPermissionRule["time_window"]> = {};
  if (request.days !== undefined) {
    if (!Array.isArray(request.days) || request.days.length > 31) {
      throw new ApiRequestValidationError("permission time window days are invalid");
    }
    timeWindow.days = request.days.map((day) => {
      if (typeof day === "number" && Number.isInteger(day) && day >= 0 && day <= 6) {
        return day;
      }
      return validateBridgeStringValue(day, "permission time window day", 32, { allowEmpty: false, trim: true });
    });
  }
  if (request.start !== undefined) {
    timeWindow.start = validateBridgeStringValue(request.start, "permission time window start", 16, {
      allowEmpty: false,
      trim: true
    });
  }
  if (request.end !== undefined) {
    timeWindow.end = validateBridgeStringValue(request.end, "permission time window end", 16, {
      allowEmpty: false,
      trim: true
    });
  }
  if (request.timezone !== undefined) {
    timeWindow.timezone = validateBridgeStringValue(request.timezone, "permission time window timezone", 128, {
      allowEmpty: true,
      trim: true
    });
  }
  return timeWindow;
}
