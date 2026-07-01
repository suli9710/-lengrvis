import { API_REQUEST_SECURITY_LIMITS } from "../../../shared/ipc";
import type { ApiQueryValue } from "../../../shared/types";
import { ApiRequestValidationError } from "./errors";
import { assertSafeFieldName, isPlainRecord, utf8ByteLength } from "./jsonSafety";
import type { ValidatedApiRequest } from "./apiRequestTypes";

export function validateApiQuery(value: unknown): ValidatedApiRequest["query"] {
  if (value === undefined) {
    return undefined;
  }
  if (!isPlainRecord(value)) {
    throw new ApiRequestValidationError("Renderer API query must be an object");
  }

  const entries = Object.entries(value);
  if (entries.length > API_REQUEST_SECURITY_LIMITS.maxQueryParams) {
    throw new ApiRequestValidationError("Renderer API query has too many parameters");
  }

  let totalBytes = 0;
  const query: NonNullable<ValidatedApiRequest["query"]> = {};
  for (const [key, queryValue] of entries) {
    assertSafeFieldName(key, "Renderer API query key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (queryValue === null || queryValue === undefined) {
      continue;
    }
    if (!["string", "number", "boolean"].includes(typeof queryValue)) {
      throw new ApiRequestValidationError("Renderer API query values must be primitive");
    }
    if (typeof queryValue === "number" && !Number.isFinite(queryValue)) {
      throw new ApiRequestValidationError("Renderer API query number is invalid");
    }
    const stringValue = String(queryValue);
    const valueBytes = utf8ByteLength(stringValue);
    if (valueBytes > API_REQUEST_SECURITY_LIMITS.maxQueryValueChars) {
      throw new ApiRequestValidationError("Renderer API query value is too large");
    }
    totalBytes += utf8ByteLength(key) + valueBytes;
    query[key] = queryValue as Exclude<ApiQueryValue, null | undefined>;
  }

  if (totalBytes > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new ApiRequestValidationError("Renderer API query is too large");
  }

  return Object.keys(query).length ? query : undefined;
}

export function validateApiTimeout(value: unknown): number {
  if (value === undefined) {
    return 30_000;
  }
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    !Number.isInteger(value) ||
    value <= 0 ||
    value > API_REQUEST_SECURITY_LIMITS.maxTimeoutMs
  ) {
    throw new ApiRequestValidationError("Renderer API timeout is invalid");
  }
  return value;
}
