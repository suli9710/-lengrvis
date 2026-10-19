import { win32 } from "node:path";
import type { AutonomyScopeInput, ButlerArrangementInput, ButlerControlAction, ButlerMatterControlAction, ButlerMessageInput, ButlerResponseInput } from "../../../shared/butlerTypes";
import {
  ApiRequestValidationError, assertJsonSafeValue, rejectUnexpectedBridgeKeys,
  validateBridgeEnum, validateBridgeIdentifier, validateBridgePositiveInteger,
  validateBridgeStringArray, validateBridgeStringValue, validatePlainBridgeBody
} from "./primitives";

const modes = new Set(["privacy", "efficiency", "hybrid"]);
const scopeActions = new Set(["observe", "file_modify", "ui_edit", "browser_prepare"]);

function bodyWithKeys(value: unknown, keys: string[]): Record<string, unknown> {
  const body = validatePlainBridgeBody(value, "butler request");
  rejectUnexpectedBridgeKeys(body, new Set(keys), "butler request");
  return body;
}

function message(value: unknown, allowEmpty = false): string {
  // Multiline user input is expected; disallow other control characters.
  if (typeof value !== "string" || value.length > 12_000 || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(value) || (!allowEmpty && !value.trim())) {
    throw new ApiRequestValidationError("butler message is invalid");
  }
  return value.trim();
}

export function validateButlerMessage(value: unknown): ButlerMessageInput {
  const body = bodyWithKeys(value, ["message", "mode", "matter_id"]);
  return {
    message: message(body.message),
    mode: validateBridgeEnum(body.mode, "butler mode", modes, "efficiency"),
    ...(body.matter_id ? { matter_id: validateBridgeIdentifier(body.matter_id, "matter id") } : {})
  };
}

export function validateButlerResponse(value: unknown): ButlerResponseInput & { matter_id: string } {
  const body = bodyWithKeys(value, ["matter_id", "version", "interaction_id", "message", "answers"]);
  const answers = body.answers === undefined ? {} : validatePlainBridgeBody(body.answers, "butler answers");
  assertJsonSafeValue(answers, 0, new WeakSet());
  if (JSON.stringify(answers).length > 24_000) throw new ApiRequestValidationError("butler answers are too large");
  return {
    matter_id: validateBridgeIdentifier(body.matter_id, "matter id"),
    version: requiredVersion(body.version),
    interaction_id: validateBridgeIdentifier(body.interaction_id, "interaction id"),
    message: message(body.message ?? "", true),
    answers
  };
}

export function validateButlerControl(value: unknown): { action: ButlerControlAction } {
  const body = bodyWithKeys(value, ["action"]);
  return { action: validateBridgeEnum(body.action, "butler action", new Set(["enable", "disable", "pause", "resume", "coordination_enable", "coordination_disable", "auto_memory_enable", "auto_memory_disable"])) };
}

export function validateButlerArrangement(value: unknown): ButlerArrangementInput {
  const body = bodyWithKeys(value, ["matter_id", "version", "depends_on", "priority", "due_at"]);
  const result: ButlerArrangementInput = {
    matter_id: validateBridgeIdentifier(body.matter_id, "matter id"), version: requiredVersion(body.version)
  };
  if (body.depends_on !== undefined) result.depends_on = validateBridgeStringArray(body.depends_on, "dependencies", 32, 256)
    .map((id) => validateBridgeIdentifier(id, "dependency id"));
  if (body.priority !== undefined) {
    if (typeof body.priority !== "number" || !Number.isInteger(body.priority) || body.priority < 0 || body.priority > 100) throw new ApiRequestValidationError("priority must be an integer from 0 to 100");
    result.priority = body.priority;
  }
  if (body.due_at !== undefined) {
    const value = validateBridgeStringValue(body.due_at, "deadline", 64, { allowEmpty: true });
    if (value && (!/(Z|[+-]\d{2}:\d{2})$/.test(value) || Number.isNaN(Date.parse(value)))) throw new ApiRequestValidationError("deadline must include a timezone");
    result.due_at = value;
  }
  return result;
}

export function validateButlerMatterControl(value: unknown): { matter_id: string; action: ButlerMatterControlAction } {
  const body = bodyWithKeys(value, ["matter_id", "action"]);
  return {
    matter_id: validateBridgeIdentifier(body.matter_id, "matter id"),
    action: validateBridgeEnum(body.action, "matter action", new Set(["pause", "resume", "cancel"]))
  };
}

export function validateButlerVersionedId(value: unknown, key: "matter_id" | "scope_id"): { id: string; version: number } {
  const body = bodyWithKeys(value, [key, "version"]);
  return { id: validateBridgeIdentifier(body[key], key), version: requiredVersion(body.version) };
}

function requiredVersion(value: unknown): number {
  if (value === undefined) throw new ApiRequestValidationError("butler version is required");
  return validateBridgePositiveInteger(value, "butler version", 1, 1, Number.MAX_SAFE_INTEGER);
}

export function validateButlerOpenArtifact(value: unknown): { matter_id: string; artifact_id: string; version: number } {
  const body = bodyWithKeys(value, ["matter_id", "artifact_id", "version"]);
  return {
    matter_id: validateBridgeIdentifier(body.matter_id, "matter id"),
    artifact_id: validateBridgeIdentifier(body.artifact_id, "artifact id"),
    version: requiredVersion(body.version)
  };
}

export function validateButlerScope(value: unknown): AutonomyScopeInput {
  const body = bodyWithKeys(value, ["id", "version", "label", "directories", "applications", "origins", "allowed_actions", "account_id"]);
  if (Boolean(body.id) !== (body.version !== undefined)) throw new ApiRequestValidationError("scope updates require id and version");
  const directories = validateBridgeStringArray(body.directories ?? [], "directories", 32, 4096);
  for (const directory of directories) {
    if (!win32.isAbsolute(directory) || /[?*]/.test(directory)) throw new ApiRequestValidationError("scope directories must be absolute paths without wildcards");
  }
  const applications = validateBridgeStringArray(body.applications ?? [], "applications", 32, 4096);
  const origins = validateBridgeStringArray(body.origins ?? [], "origins", 32, 2048).map((origin) => {
    let url: URL;
    try { url = new URL(origin); } catch { throw new ApiRequestValidationError("scope origin is invalid"); }
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.pathname !== "/" || url.search || url.hash || origin.includes("*")) {
      throw new ApiRequestValidationError("scope origins require exact HTTP(S) origins without paths or credentials");
    }
    return url.origin;
  });
  const allowed_actions = validateBridgeStringArray(body.allowed_actions ?? ["observe"], "allowed actions", 4, 32)
    .map((action) => validateBridgeEnum<AutonomyScopeInput["allowed_actions"][number]>(action, "scope action", scopeActions));
  if (!directories.length && !applications.length && !origins.length) throw new ApiRequestValidationError("an autonomy scope requires a directory, application or website");
  if (!allowed_actions.length) throw new ApiRequestValidationError("an autonomy scope requires an allowed action");
  return {
    ...(body.id ? { id: validateBridgeIdentifier(body.id, "scope id"), version: requiredVersion(body.version) } : {}),
    label: validateBridgeStringValue(body.label, "scope label", 160, { trim: true }),
    directories: [...new Set(directories)], applications: [...new Set(applications)], origins: [...new Set(origins)],
    allowed_actions: [...new Set(allowed_actions)],
    account_id: validateBridgeStringValue(body.account_id ?? "", "scope account", 256, { allowEmpty: true, trim: true })
  };
}
