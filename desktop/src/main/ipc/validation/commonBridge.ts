import type {
  DesktopHardwareAccelerationSmokeRequest,
  DesktopPerceptionSuggestionLaunchRequest,
  DesktopRunStartRequest
} from "../../../shared/types";
import {
  ApiRequestValidationError,
  rejectUnexpectedBridgeKeys,
  validateBridgeEnum,
  validateBridgeIdentifier,
  validateBridgePathValue,
  validateBridgePositiveInteger,
  validateBridgeStringValue,
  validatePlainBridgeBody
} from "./primitives";

const RUN_MODES = new Set(["privacy", "efficiency", "hybrid"]);
const RUN_ENGINES = new Set(["auto", "os", "developer"]);
const HARDWARE_SMOKE_OPERATIONS = new Set(["warmup", "test_generate", "test_embedding", "test_ocr", "test_image_embedding"]);

export function validateCommandExecuteRequest(value: unknown): { name: string; args: Record<string, unknown> } {
  const request = validatePlainBridgeBody(value, "command execute request");
  const name = validateBridgeIdentifier(request.name, "command name");
  const args = request.args === undefined ? {} : validatePlainBridgeBody(request.args, "command args");
  return { name, args };
}

export function validateOptionalModelRequest(value: unknown, label: string): { model?: string } {
  const request = validatePlainBridgeBody(value, label);
  if (request.model === undefined || request.model === null || request.model === "") {
    return {};
  }
  if (typeof request.model !== "string") {
    throw new ApiRequestValidationError("model must be a string");
  }
  const model = request.model.trim();
  if (!model || model.length > 256 || /[\s\u0000-\u001F\u007F]/.test(model)) {
    throw new ApiRequestValidationError("model is invalid");
  }
  return { model };
}

export function validateRunStartRequest(value: unknown): DesktopRunStartRequest {
  const request = validatePlainBridgeBody(value, "run start request");
  const message = validateBridgeStringValue(request.message, "run message", 20_000, {
    allowEmpty: false,
    trim: true
  });
  const mode = validateBridgeEnum<DesktopRunStartRequest["mode"] & string>(
    request.mode,
    "run mode",
    RUN_MODES,
    "efficiency"
  );
  const engine = validateBridgeEnum<DesktopRunStartRequest["engine"] & string>(
    request.engine,
    "run engine",
    RUN_ENGINES,
    "auto"
  );
  return { message, mode, engine };
}

export function validatePerceptionSuggestionLaunchRequest(
  value: unknown
): DesktopPerceptionSuggestionLaunchRequest {
  const request = validatePlainBridgeBody(value, "perception suggestion launch request");
  rejectUnexpectedBridgeKeys(
    request,
    new Set(["suggestionId", "suggestion_id", "mode"]),
    "perception suggestion launch request"
  );
  const suggestionId = validateBridgeIdentifier(request.suggestionId ?? request.suggestion_id, "suggestion id");
  const mode = validateBridgeEnum<DesktopPerceptionSuggestionLaunchRequest["mode"] & string>(
    request.mode,
    "suggestion launch mode",
    RUN_MODES,
    "efficiency"
  );
  return { suggestionId, mode };
}

export function validateHardwareAccelerationSmokeRequest(
  value: unknown
): Required<Pick<DesktopHardwareAccelerationSmokeRequest, "operation" | "prompt" | "maxTokens" | "texts">> &
  Pick<DesktopHardwareAccelerationSmokeRequest, "modelPath" | "imagePath"> {
  const request = value === undefined || value === null
    ? {}
    : validatePlainBridgeBody(value, "hardware acceleration smoke request");
  rejectUnexpectedBridgeKeys(
    request,
    new Set(["operation", "prompt", "maxTokens", "max_tokens", "texts", "modelPath", "model_path", "imagePath", "image_path"]),
    "hardware acceleration smoke request"
  );
  const operation = validateBridgeEnum<Required<DesktopHardwareAccelerationSmokeRequest>["operation"]>(
    request.operation,
    "hardware smoke operation",
    HARDWARE_SMOKE_OPERATIONS,
    "warmup"
  );
  const prompt = request.prompt === undefined || request.prompt === null
    ? ""
    : validateBridgeStringValue(request.prompt, "hardware smoke prompt", 512, { allowEmpty: true, trim: true });
  const maxTokens = validateBridgePositiveInteger(request.maxTokens ?? request.max_tokens, "hardware smoke max tokens", 16, 1, 64);
  const rawTexts = request.texts === undefined || request.texts === null ? [] : request.texts;
  if (!Array.isArray(rawTexts) || rawTexts.length > 4) {
    throw new ApiRequestValidationError("hardware smoke texts are invalid");
  }
  const texts = rawTexts.map((item, index) =>
    validateBridgeStringValue(item, `hardware smoke texts[${index}]`, 512, { allowEmpty: false, trim: true })
  );
  const modelPath = request.modelPath === undefined && request.model_path === undefined
    ? undefined
    : validateBridgePathValue(request.modelPath ?? request.model_path, "hardware smoke model path");
  const imagePath = request.imagePath === undefined && request.image_path === undefined
    ? undefined
    : validateBridgePathValue(request.imagePath ?? request.image_path, "hardware smoke image path");
  return { operation, prompt, maxTokens, texts, modelPath, imagePath };
}

export function validateNoPayloadBridgeRequest(value: unknown, label: string): void {
  if (value !== undefined && value !== null) {
    throw new ApiRequestValidationError(`${label} does not accept a payload`);
  }
}

export function validateBrowserSessionRequest(value: unknown): { sessionId: string } {
  const request = validatePlainBridgeBody(value, "browser session request");
  rejectUnexpectedBridgeKeys(request, new Set(["sessionId", "session_id"]), "browser session request");
  return {
    sessionId: validateBridgeIdentifier(request.sessionId ?? request.session_id, "browser session id")
  };
}
