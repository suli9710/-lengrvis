import {
  API_REQUEST_DENIED_EXACT_PATHS,
  API_REQUEST_DENIED_METHOD_PATHS,
  API_REQUEST_DENIED_PATH_PREFIXES,
  API_REQUEST_SECURITY_LIMITS
} from "../../../shared/ipc";
import type { ApiMethod } from "../../../shared/types";
import { assertLoopbackBackendUrl } from "../../backendUrl";
import { ApiRequestValidationError } from "./errors";
import type { ApiRequestValidationOptions, ValidatedApiRequest } from "./apiRequestTypes";

const API_REQUEST_DENIED_EXACT_PATH_SET = new Set<string>(API_REQUEST_DENIED_EXACT_PATHS);
const API_REQUEST_DENIED_METHOD_PATH_RULES = API_REQUEST_DENIED_METHOD_PATHS;

export function buildValidatedRequestUrl(baseUrl: string, request: ValidatedApiRequest): URL {
  const backendUrl = loopbackBackendUrlForApiRequest(baseUrl);

  const backendOrigin = backendUrl.origin;
  const url = new URL(request.endpoint, backendUrl);
  if (url.origin !== backendOrigin) {
    throw new ApiRequestValidationError("Renderer API request escaped the configured backend origin");
  }

  for (const [key, value] of Object.entries(request.query ?? {})) {
    url.searchParams.set(key, String(value));
  }
  if (url.search.length > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new ApiRequestValidationError("Renderer API query is too large");
  }

  return url;
}

export function validateApiEndpoint(
  value: unknown,
  method: ApiMethod,
  options: ApiRequestValidationOptions = {}
): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError("Renderer API endpoint is required");
  }
  if (!value || value.length > API_REQUEST_SECURITY_LIMITS.maxEndpointChars) {
    throw new ApiRequestValidationError("Renderer API endpoint length is invalid");
  }
  if (value.trim() !== value || /\s|[\u0000-\u001F\u007F]/.test(value)) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe characters");
  }
  if (value.includes("?") || value.includes("#")) {
    throw new ApiRequestValidationError("Renderer API endpoint must not include query strings or fragments");
  }
  if (
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("//") ||
    value.includes("\\") ||
    /^[a-z][a-z0-9+.-]*:/i.test(value)
  ) {
    throw new ApiRequestValidationError("Renderer API requests must use backend-relative endpoints");
  }
  if (/%2f|%5c/i.test(value)) {
    throw new ApiRequestValidationError("Renderer API endpoint must not contain encoded path separators");
  }

  let decodedPath: string;
  try {
    decodedPath = decodeURIComponent(value);
  } catch {
    throw new ApiRequestValidationError("Renderer API endpoint encoding is invalid");
  }

  if (decodedPath.includes("\\") || decodedPath.includes("//")) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe path separators");
  }
  if (decodedPath !== "/api" && !decodedPath.startsWith("/api/")) {
    throw new ApiRequestValidationError("Renderer API requests must target backend API paths");
  }

  const segments = decodedPath.split("/");
  if (segments.some((segment) => segment === "." || segment === "..")) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe path segments");
  }

  const normalizedPath = `/${segments.filter(Boolean).join("/")}`;
  if (!options.allowDeniedDesktopBridgePath) {
    rejectDeniedApiPath(normalizedPath, method);
  }
  return value;
}

function loopbackBackendUrlForApiRequest(baseUrl: string): URL {
  try {
    return assertLoopbackBackendUrl(baseUrl, "Desktop API token request");
  } catch (error) {
    throw new ApiRequestValidationError(
      error instanceof Error
        ? error.message
        : "Desktop API token requests require a loopback backend base URL"
    );
  }
}

function rejectDeniedApiPath(pathname: string, method: ApiMethod): void {
  if (API_REQUEST_DENIED_EXACT_PATH_SET.has(pathname)) {
    throw new ApiRequestValidationError("Renderer API endpoint requires an explicit desktop bridge");
  }
  if (API_REQUEST_DENIED_PATH_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))) {
    throw new ApiRequestValidationError("Renderer API endpoint requires an explicit desktop bridge");
  }
  if (
    API_REQUEST_DENIED_METHOD_PATH_RULES.some((rule) => {
      if (rule.method !== method) {
        return false;
      }
      if ("path" in rule) {
        return pathname === rule.path;
      }
      if (!pathname.startsWith(rule.pathPrefix)) {
        return false;
      }
      if ("pathSuffix" in rule) {
        return pathname.endsWith(rule.pathSuffix);
      }
      return true;
    })
  ) {
    throw new ApiRequestValidationError("Renderer API endpoint requires an explicit desktop bridge");
  }
}
