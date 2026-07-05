const DEFAULT_ERROR_MESSAGE = "Backend request failed";
const NEXT_ACTION_PREFIX = "下一步：";

export function backendErrorMessage(data: unknown, fallback = DEFAULT_ERROR_MESSAGE): string {
  const structured = structuredBackendErrorMessage(data);
  if (structured) return structured;

  if (data && typeof data === "object") {
    const direct = (data as { message?: unknown }).message;
    if (isNonEmptyString(direct)) return direct.trim();

    const detail = (data as { detail?: unknown }).detail;
    if (isNonEmptyString(detail)) return detail.trim();

    const nested = (data as { error?: { message?: unknown } }).error?.message;
    if (isNonEmptyString(nested)) return nested.trim();
  }

  return fallback.trim() || DEFAULT_ERROR_MESSAGE;
}

export function structuredBackendErrorMessage(data: unknown): string {
  if (!data || typeof data !== "object" || !("detail" in data)) {
    return "";
  }

  const detail = (data as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") {
    return "";
  }

  const message = (detail as { message?: unknown }).message;
  if (!isNonEmptyString(message)) {
    return "";
  }

  const nextAction = (detail as { next_action?: unknown }).next_action;
  if (isNonEmptyString(nextAction)) {
    return `${message.trim()} ${NEXT_ACTION_PREFIX}${nextAction.trim()}`;
  }

  return message.trim();
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
