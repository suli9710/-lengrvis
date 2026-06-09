const RAW_STRUCTURED_PAYLOAD_PATTERN = /(^\s*[{[]|["']?(?:args|tool_args|arguments|headers|authorization|protocol|host|hostname|base_url|url)["']?\s*:)/i;
const BEARER_TOKEN_PATTERN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const TOKEN_VALUE_PATTERN = /\b(?:secret|session|access|refresh|grant|api|mobile)[-_]token(?:[-_][A-Za-z0-9._-]+)?\b/gi;
const SECRET_ASSIGNMENT_PATTERN = /\b(token|access_token|auth|authorization|api[_-]?key|secret|password|session[_-]?token)\b\s*[:=]\s*["']?[^"',;\s})\]]+/gi;
const TOOL_ARGS_PATTERN = /\b(args|tool_args|arguments)\b\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\n;]+)/gi;
const URL_PATTERN = /\b(?:https?|wss?|file):\/\/[^\s,;)\]}>]+/gi;
const HOST_PATTERN = /\b(?:localhost|(?:\d{1,3}\.){3}\d{1,3})(?::\d{2,5})?\b/gi;
const WINDOWS_PATH_PATTERN = /\b[A-Za-z]:[\\/][^\s,;)\]}>]+(?:[\\/][^\s,;)\]}>]+)*/g;
const UNC_PATH_PATTERN = /\\\\[^\\/\s]+\\[^\s,;)\]}>]+/g;
const POSIX_PATH_PATTERN = /(^|[\s(["'])\/(?:Users|home|var|tmp|etc|mnt|Volumes|private|opt|usr|workspace|root|srv|Desktop|Downloads)\b[^\s,;)\]}>]*/gi;
const HOST_ASSIGNMENT_PATTERN = /\b(host|hostname|origin|base_url|server_url|url|protocol|scheme)\b\s*[:=]\s*["']?[^"',;\s})\]]+/gi;

export function safeDisplayText(value: unknown, fallback = "内容已隐藏，避免在手机上暴露本机细节。"): string {
  const raw = textValue(value).replace(/\u0000/g, " ").trim();
  if (!raw) return fallback;
  if (looksLikeRawStructuredPayload(raw)) return fallback;
  const redacted = redactSensitiveText(raw).replace(/[ \t]{3,}/g, "  ").trim();
  return redacted || fallback;
}

export function safeCompactText(value: unknown, fallback = "内容已隐藏"): string {
  return safeDisplayText(value, fallback).replace(/\s+/g, " ").trim();
}

export function safePreviewText(value: unknown, fallback = "手机上隐藏了原始预览，避免暴露本机路径或参数。请在电脑端查看细节。"): string {
  const raw = textValue(value).trim();
  if (!raw || raw === "暂无预览内容") return "打开后查看详情。";
  if (looksLikeRawStructuredPayload(raw)) return fallback;
  const redacted = redactSensitiveText(raw).trim();
  if (!redacted) return fallback;
  return redacted;
}

export function containsSensitiveDisplayText(value: unknown): boolean {
  const raw = textValue(value);
  return (
    matches(BEARER_TOKEN_PATTERN, raw) ||
    matches(TOKEN_VALUE_PATTERN, raw) ||
    matches(SECRET_ASSIGNMENT_PATTERN, raw) ||
    matches(TOOL_ARGS_PATTERN, raw) ||
    matches(URL_PATTERN, raw) ||
    matches(HOST_PATTERN, raw) ||
    matches(WINDOWS_PATH_PATTERN, raw) ||
    matches(UNC_PATH_PATTERN, raw) ||
    matches(POSIX_PATH_PATTERN, raw) ||
    matches(HOST_ASSIGNMENT_PATTERN, raw) ||
    matches(RAW_STRUCTURED_PAYLOAD_PATTERN, raw)
  );
}

function redactSensitiveText(value: string): string {
  return value
    .replace(BEARER_TOKEN_PATTERN, "Bearer [已隐藏]")
    .replace(TOKEN_VALUE_PATTERN, "[已隐藏的 token]")
    .replace(SECRET_ASSIGNMENT_PATTERN, "$1=[已隐藏]")
    .replace(TOOL_ARGS_PATTERN, "$1=[已隐藏]")
    .replace(URL_PATTERN, "[已隐藏的连接地址]")
    .replace(HOST_ASSIGNMENT_PATTERN, "$1=[已隐藏]")
    .replace(HOST_PATTERN, "[已隐藏的电脑地址]")
    .replace(WINDOWS_PATH_PATTERN, "[已隐藏的本机路径]")
    .replace(UNC_PATH_PATTERN, "[已隐藏的本机路径]")
    .replace(POSIX_PATH_PATTERN, "$1[已隐藏的本机路径]");
}

function looksLikeRawStructuredPayload(value: string): boolean {
  if (!RAW_STRUCTURED_PAYLOAD_PATTERN.test(value)) return false;
  return containsRawSensitiveMarker(value) || value.length > 80;
}

function containsRawSensitiveMarker(value: string): boolean {
  return /(args|tool_args|arguments|headers|authorization|token|password|secret|host|hostname|protocol|base_url|url|path)/i.test(value);
}

function matches(pattern: RegExp, value: string): boolean {
  pattern.lastIndex = 0;
  return pattern.test(value);
}

function textValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}
