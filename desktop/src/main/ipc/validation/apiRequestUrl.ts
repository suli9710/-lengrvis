import { isRendererApiRouteAllowed } from "../../../shared/apiRequestAllowlist";
import { API_REQUEST_SECURITY_LIMITS } from "../../../shared/ipc";
import type { ApiMethod } from "../../../shared/types";
import { assertLoopbackBackendUrl } from "../../backendUrl";
import { ApiRequestValidationError } from "./errors";
import type { ApiRequestValidationOptions, ValidatedApiRequest } from "./apiRequestTypes";

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

  if (decodedPath.trim() !== decodedPath || /\s|[\u0000-\u001F\u007F]/.test(decodedPath)) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe decoded characters");
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
  if (!options.allowExplicitDesktopBridgePath && !isRendererApiRouteAllowed(normalizedPath, method)) {
    throw new ApiRequestValidationError(
      "Renderer API endpoint requires an explicit desktop bridge because the method and route are not allowlisted"
    );
  }
  return value;
}

function loopbackBackendUrlForApiRequest(baseUrl: string): URL {
  try {
    return assertLoopbackBackendUrl(baseUrl, "Desktop API token request");
  } catch (error) { // broad-exception-boundary
    throw new ApiRequestValidationError(
      error instanceof Error
        ? error.message
        : "Desktop API token requests require a loopback backend base URL"
    );
  }
}
