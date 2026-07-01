import type { ApiMethod } from "../../../shared/types";
import { API_REQUEST_SECURITY_LIMITS } from "../../../shared/ipc";
import { ApiRequestValidationError } from "./errors";
import { assertJsonSafeValue, utf8ByteLength } from "./jsonSafety";

export function serializeApiRequestBody(request: Record<string, unknown>, method: ApiMethod): string | undefined {
  if (!Object.prototype.hasOwnProperty.call(request, "body") || request.body === undefined) {
    return undefined;
  }
  if (method === "GET") {
    throw new ApiRequestValidationError("Renderer API GET requests cannot include a body");
  }

  assertJsonSafeValue(request.body, 0, new WeakSet<object>());
  const serialized = JSON.stringify(request.body);
  if (typeof serialized !== "string") {
    throw new ApiRequestValidationError("Renderer API body must be JSON serializable");
  }
  if (utf8ByteLength(serialized) > API_REQUEST_SECURITY_LIMITS.maxBodyBytes) {
    throw new ApiRequestValidationError("Renderer API body is too large");
  }
  return serialized;
}
