import { API_REQUEST_ALLOWED_KEYS, API_REQUEST_SECURITY_LIMITS } from "../shared/ipc";
import type { ApiMethod, ApiQueryValue, ApiRequest } from "../shared/types";

const apiRequestAllowedKeys = new Set<string>(API_REQUEST_ALLOWED_KEYS);
const apiRequestAllowedMethods = new Set<ApiMethod>(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const apiRequestReservedKeys = new Set(["__proto__", "constructor", "prototype"]);

export function sanitizeApiBridgeRequest<TBody>(request: ApiRequest<TBody>): ApiRequest<TBody> {
  const requestRecord = clonePlainDataRecord(request, "Renderer API request");

  for (const key of Object.keys(requestRecord)) {
    if (!apiRequestAllowedKeys.has(key)) {
      throw new Error(`Renderer API request field is not allowed: ${key}`);
    }
  }

  if (typeof requestRecord.endpoint !== "string") {
    throw new Error("Renderer API endpoint is required");
  }

  const sanitized: ApiRequest<TBody> = { endpoint: requestRecord.endpoint };
  if (requestRecord.method !== undefined) {
    sanitized.method = sanitizeApiMethod(requestRecord.method);
  }
  if (requestRecord.query !== undefined) {
    sanitized.query = sanitizeApiQuery(requestRecord.query);
  }
  if (Object.prototype.hasOwnProperty.call(requestRecord, "body")) {
    sanitized.body = (
      requestRecord.body === undefined
        ? undefined
        : sanitizeApiBodyValue(requestRecord.body, "Renderer API body", 0, new WeakSet<object>())
    ) as TBody;
  }
  if (requestRecord.timeoutMs !== undefined) {
    sanitized.timeoutMs = sanitizeApiTimeout(requestRecord.timeoutMs);
  }
  if (requestRecord.abortGroup !== undefined) {
    sanitized.abortGroup = sanitizeApiAbortGroup(requestRecord.abortGroup);
  }
  return sanitized;
}

export function sanitizeApiAbortGroup(value: unknown): string {
  if (typeof value !== "string") {
    throw new Error("Renderer API abort group is invalid");
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 64 || !/^[A-Za-z0-9._-]+$/.test(trimmed)) {
    throw new Error("Renderer API abort group is invalid");
  }
  return trimmed;
}

function sanitizeApiMethod(value: unknown): ApiMethod {
  if (typeof value !== "string" || !apiRequestAllowedMethods.has(value as ApiMethod)) {
    throw new Error("Renderer API request method is not allowed");
  }
  return value as ApiMethod;
}

function sanitizeApiTimeout(value: unknown): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    !Number.isInteger(value) ||
    value <= 0 ||
    value > API_REQUEST_SECURITY_LIMITS.maxTimeoutMs
  ) {
    throw new Error("Renderer API timeout is invalid");
  }
  return value;
}

function sanitizeApiQuery(value: unknown): Record<string, ApiQueryValue> {
  const queryRecord = clonePlainDataRecord(value, "Renderer API query");
  const entries = Object.entries(queryRecord);
  if (entries.length > API_REQUEST_SECURITY_LIMITS.maxQueryParams) {
    throw new Error("Renderer API query has too many parameters");
  }

  const query: Record<string, ApiQueryValue> = {};
  let totalChars = 0;
  for (const [key, queryValue] of entries) {
    assertSafeApiFieldName(key, "Renderer API query key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (queryValue === null || queryValue === undefined) {
      query[key] = queryValue;
      continue;
    }
    if (!["string", "number", "boolean"].includes(typeof queryValue)) {
      throw new Error("Renderer API query values must be primitive");
    }
    if (typeof queryValue === "number" && !Number.isFinite(queryValue)) {
      throw new Error("Renderer API query number is invalid");
    }
    const stringValue = String(queryValue);
    if (stringValue.length > API_REQUEST_SECURITY_LIMITS.maxQueryValueChars) {
      throw new Error("Renderer API query value is too large");
    }
    totalChars += key.length + stringValue.length;
    query[key] = queryValue as ApiQueryValue;
  }

  if (totalChars > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new Error("Renderer API query is too large");
  }

  return query;
}

type SanitizedApiBodyValue =
  | string
  | number
  | boolean
  | null
  | SanitizedApiBodyValue[]
  | { [key: string]: SanitizedApiBodyValue };

function sanitizeApiBodyValue(
  value: unknown,
  label: string,
  depth: number,
  seen: WeakSet<object>
): SanitizedApiBodyValue {
  if (depth > API_REQUEST_SECURITY_LIMITS.maxBodyDepth) {
    throw new Error("Renderer API body is too deeply nested");
  }

  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("Renderer API body number is invalid");
    }
    return value;
  }
  if (typeof value !== "object") {
    throw new Error("Renderer API body must be plain JSON data");
  }
  if (seen.has(value)) {
    throw new Error("Renderer API body cannot be circular");
  }

  seen.add(value);
  try {
    if (Array.isArray(value)) {
      return sanitizeApiBodyArray(value, label, depth, seen);
    }
    const record = clonePlainDataRecord(value, label);
    const entries = Object.entries(record);
    if (entries.length > API_REQUEST_SECURITY_LIMITS.maxBodyObjectKeys) {
      throw new Error("Renderer API body object has too many keys");
    }

    const sanitized: { [key: string]: SanitizedApiBodyValue } = {};
    for (const [key, item] of entries) {
      assertSafeApiFieldName(key, "Renderer API body key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
      sanitized[key] = sanitizeApiBodyValue(item, `${label}.${key}`, depth + 1, seen);
    }
    return sanitized;
  } finally {
    seen.delete(value);
  }
}

function sanitizeApiBodyArray(
  value: unknown[],
  label: string,
  depth: number,
  seen: WeakSet<object>
): SanitizedApiBodyValue[] {
  if (value.length > API_REQUEST_SECURITY_LIMITS.maxBodyArrayItems) {
    throw new Error("Renderer API body array is too large");
  }
  rejectUnexpectedArrayFields(value, label);

  const sanitized: SanitizedApiBodyValue[] = [];
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(value, index)) {
      throw new Error("Renderer API body array must not be sparse");
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
    if (!descriptor || !descriptor.enumerable || !("value" in descriptor)) {
      throw new Error("Renderer API body array must contain data values");
    }
    sanitized.push(sanitizeApiBodyValue(descriptor.value, `${label}[${index}]`, depth + 1, seen));
  }
  return sanitized;
}

function clonePlainDataRecord(value: unknown, label: string): Record<string, unknown> {
  if (!isPlainRecord(value)) {
    throw new Error(`${label} must be a plain object`);
  }

  const sanitized: Record<string, unknown> = {};
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key === "symbol") {
      throw new Error(`${label} must not contain symbol keys`);
    }
    assertSafeApiFieldName(key, `${label} key`, API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor) {
      continue;
    }
    if (!descriptor.enumerable) {
      throw new Error(`${label} must not contain non-enumerable fields`);
    }
    if (!("value" in descriptor)) {
      throw new Error(`${label} must not contain accessor fields`);
    }
    sanitized[key] = descriptor.value;
  }
  return sanitized;
}

function rejectUnexpectedArrayFields(value: unknown[], label: string): void {
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key === "symbol") {
      throw new Error(`${label} must not contain symbol keys`);
    }
    if (key === "length") {
      continue;
    }
    const index = Number(key);
    if (!Number.isInteger(index) || index < 0 || index >= value.length || String(index) !== key) {
      throw new Error("Renderer API body array must not contain object fields");
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !descriptor.enumerable || !("value" in descriptor)) {
      throw new Error("Renderer API body array must contain data values");
    }
  }
}

function assertSafeApiFieldName(name: string, label: string, maxChars: number): void {
  if (!name || name.length > maxChars || /[\u0000-\u001F\u007F]/.test(name) || apiRequestReservedKeys.has(name)) {
    throw new Error(`${label} is invalid`);
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
