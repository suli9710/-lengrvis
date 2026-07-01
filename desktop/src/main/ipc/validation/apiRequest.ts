import { API_REQUEST_ALLOWED_KEYS } from "../../../shared/ipc";
import type { ApiMethod, ApiRequest } from "../../../shared/types";
import { ApiRequestValidationError, isPlainRecord } from "./primitives";
import { serializeApiRequestBody } from "./apiRequestBody";
import { validateApiQuery, validateApiTimeout } from "./apiRequestQuery";
import { buildValidatedRequestUrl, validateApiEndpoint } from "./apiRequestUrl";
import type { ApiRequestValidationOptions, ValidatedApiRequest } from "./apiRequestTypes";

const ALLOWED_API_METHODS = new Set<ApiMethod>(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const API_REQUEST_ALLOWED_KEY_SET = new Set<string>(API_REQUEST_ALLOWED_KEYS);

export type { ApiRequestValidationOptions, ValidatedApiRequest } from "./apiRequestTypes";
export { buildValidatedRequestUrl } from "./apiRequestUrl";

export function buildRequestUrl(baseUrl: string, request: ApiRequest): URL {
  return buildValidatedRequestUrl(baseUrl, validateApiRequest(request));
}

export function validateApiRequest(
  request: unknown,
  options: ApiRequestValidationOptions = {}
): ValidatedApiRequest {
  if (!isPlainRecord(request)) {
    throw new ApiRequestValidationError("Renderer API request is malformed");
  }

  rejectUnexpectedApiRequestKeys(request);
  const method = validateApiMethod(request.method);
  const endpoint = validateApiEndpoint(request.endpoint, method, options);
  const query = validateApiQuery(request.query);
  const timeoutMs = validateApiTimeout(request.timeoutMs);
  const serializedBody = serializeApiRequestBody(request, method);
  const abortGroup = validateOptionalApiAbortGroup(request.abortGroup);
  return { endpoint, method, query, serializedBody, timeoutMs, abortGroup };
}

export function validateApiAbortGroup(value: unknown): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError("Renderer API abort group is invalid");
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 64 || !/^[A-Za-z0-9._-]+$/.test(trimmed)) {
    throw new ApiRequestValidationError("Renderer API abort group is invalid");
  }
  return trimmed;
}

function validateOptionalApiAbortGroup(value: unknown): string | undefined {
  if (value === undefined) {
    return undefined;
  }
  return validateApiAbortGroup(value);
}

function rejectUnexpectedApiRequestKeys(request: Record<string, unknown>): void {
  for (const key of Object.keys(request)) {
    if (!API_REQUEST_ALLOWED_KEY_SET.has(key)) {
      const detail = key === "headers" ? "custom headers are not allowed" : `field is not allowed: ${key}`;
      throw new ApiRequestValidationError(`Renderer API request ${detail}`);
    }
  }
}

function validateApiMethod(value: unknown): ApiMethod {
  if (value === undefined) {
    return "GET";
  }
  if (typeof value !== "string" || value !== value.toUpperCase() || !ALLOWED_API_METHODS.has(value as ApiMethod)) {
    throw new ApiRequestValidationError("Renderer API request method is not allowed");
  }
  return value as ApiMethod;
}
