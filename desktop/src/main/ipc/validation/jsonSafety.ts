import { API_REQUEST_SECURITY_LIMITS } from "../../../shared/ipc";
import { ApiRequestValidationError } from "./errors";

const API_REQUEST_RESERVED_KEYS = new Set(["__proto__", "constructor", "prototype"]);

export function assertJsonSafeValue(value: unknown, depth: number, seen: WeakSet<object>): void {
  if (depth > API_REQUEST_SECURITY_LIMITS.maxBodyDepth) {
    throw new ApiRequestValidationError("Renderer API body is too deeply nested");
  }

  if (value === null) {
    return;
  }

  if (typeof value === "string") {
    if (utf8ByteLength(value) > API_REQUEST_SECURITY_LIMITS.maxBodyStringBytes) {
      throw new ApiRequestValidationError("Renderer API body string is too large");
    }
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new ApiRequestValidationError("Renderer API body number is invalid");
    }
    return;
  }
  if (typeof value === "boolean") {
    return;
  }
  if (typeof value !== "object") {
    throw new ApiRequestValidationError("Renderer API body must be JSON serializable");
  }

  if (seen.has(value)) {
    throw new ApiRequestValidationError("Renderer API body cannot be circular");
  }
  seen.add(value);

  if (Array.isArray(value)) {
    if (value.length > API_REQUEST_SECURITY_LIMITS.maxBodyArrayItems) {
      throw new ApiRequestValidationError("Renderer API body array is too large");
    }
    for (const item of value) {
      assertJsonSafeValue(item, depth + 1, seen);
    }
    seen.delete(value);
    return;
  }

  if (!isPlainRecord(value)) {
    throw new ApiRequestValidationError("Renderer API body must contain plain JSON objects");
  }

  const keys = Object.keys(value);
  if (keys.length > API_REQUEST_SECURITY_LIMITS.maxBodyObjectKeys) {
    throw new ApiRequestValidationError("Renderer API body object has too many keys");
  }
  for (const key of keys) {
    assertSafeFieldName(key, "Renderer API body key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    assertJsonSafeValue(value[key], depth + 1, seen);
  }
  seen.delete(value);
}

export function assertSafeFieldName(name: string, label: string, maxChars: number): void {
  if (!name || name.length > maxChars || /[\u0000-\u001F\u007F]/.test(name) || API_REQUEST_RESERVED_KEYS.has(name)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
}

export function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

export function utf8ByteLength(value: string): number {
  return Buffer.byteLength(value, "utf8");
}
