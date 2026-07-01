import { API_REQUEST_SECURITY_LIMITS } from "../../../shared/ipc";
import { ApiRequestValidationError } from "./errors";
import { assertSafeFieldName, isPlainRecord } from "./jsonSafety";

export function validatePlainBridgeBody(value: unknown, label: string): Record<string, unknown> {
  if (!isPlainRecord(value)) {
    throw new ApiRequestValidationError(`${label} must be an object`);
  }
  return value;
}

export function rejectUnexpectedBridgeKeys(
  request: Record<string, unknown>,
  allowedKeys: ReadonlySet<string>,
  label: string
): void {
  for (const key of Object.keys(request)) {
    assertSafeFieldName(key, `${label} key`, API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (!allowedKeys.has(key)) {
      throw new ApiRequestValidationError(`${label} field is not allowed: ${key}`);
    }
  }
}
