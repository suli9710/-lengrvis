const RAW_STRUCTURED_PAYLOAD_PATTERN = /(^\s*[{[]|["']?(?:args|tool_args|arguments|headers|authorization|protocol|host|hostname|base_url|url|path|token|access_token|session_token|refresh_token)["']?\s*[:=])/i;
const ANSI_ESCAPE_PATTERN = /\u001b\[[0-9;?]*[ -/]*[@-~]/g;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g;
const BEARER_TOKEN_PATTERN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const TOKEN_VALUE_PATTERN = /\b(?:secret|session|access|refresh|grant|api|mobile)[-_]token(?:[-_][A-Za-z0-9._-]+)?\b/gi;
const SECRET_ASSIGNMENT_PATTERN = /\b(token|access_token|auth|authorization|api[_-]?key|apiKey|secret|client_secret|clientSecret|password|passphrase|session[_-]?token)\b\s*[:=]\s*["']?[^"',;\s})\]]+/gi;
const JSON_SECRET_PATTERN = /(["'](?:token|access_token|auth|authorization|api[_-]?key|apiKey|secret|client_secret|clientSecret|password|passphrase|session[_-]?token)["']\s*:\s*)["'][^"']+["']/gi;
const TOOL_ARGS_PATTERN = /\b(args|tool_args|arguments)\b\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\n;]+)/gi;
const ENV_SECRET_ASSIGNMENT_PATTERN = /\b([A-Z][A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PASS|AUTH|CREDENTIAL)[A-Z0-9_]*)\b\s*[:=]\s*["']?[^"',;\s})\]]+/g;
const URL_PATTERN = /\b(?:https?|wss?|file):\/\/[^\s,;)\]}>]+/gi;
const HOST_PATTERN = /\b(?:localhost|(?:\d{1,3}\.){3}\d{1,3})(?::\d{2,5})?\b/gi;
const WINDOWS_PATH_PATTERN = /\b[A-Za-z]:[\\/][^\s,;)\]}>]+(?:[\\/][^\s,;)\]}>]+)*/g;
const UNC_PATH_PATTERN = /\\\\[^\\/\s]+\\[^\s,;)\]}>]+/g;
const POSIX_PATH_PATTERN = /(^|[\s(["'=])\/(?:Users|home|var|tmp|etc|mnt|Volumes|private|opt|usr|workspace|root|srv|Desktop|Downloads)\b[^\s,;)\]}>]*/gi;
const HOME_SHORTHAND_PATH_PATTERN = /(^|[\s(["'=])~[\\/][^\s,;)\]}>]*/g;
const HOST_ASSIGNMENT_PATTERN = /\b(host|hostname|origin|base_url|server_url|url|protocol|scheme)\b\s*[:=]\s*["']?[^"',;\s})\]]+/gi;
const PRIVATE_KEY_PATTERN = /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;
const CLOUD_TOKEN_PATTERN = /\b(?:sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b/g;
const LONG_SECRETISH_VALUE_PATTERN = /\b[A-Za-z0-9_=-]{32,}\.[A-Za-z0-9_.=-]{12,}\b/g;
const EMAIL_PATTERN = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
const MAX_DISPLAY_TEXT_LENGTH = 900;
const MAX_PREVIEW_TEXT_LENGTH = 1200;

export function safeDisplayText(value: unknown, fallback = "内容已隐藏，避免在手机上暴露本机细节。"): string {
  const raw = normalizeDisplayText(textValue(value)).trim();
  if (!raw) return fallback;
  if (looksLikeRawStructuredPayload(raw)) return fallback;
  const redacted = clampDisplayText(redactSensitiveText(raw).replace(/[ \t]{3,}/g, "  ").trim(), MAX_DISPLAY_TEXT_LENGTH);
  return redacted || fallback;
}

export function safeCompactText(value: unknown, fallback = "内容已隐藏"): string {
  return clampDisplayText(safeDisplayText(value, fallback).replace(/\s+/g, " ").trim(), 180);
}

export function safePreviewText(value: unknown, fallback = "手机上隐藏了原始预览，避免暴露本机路径或参数。请在电脑端查看细节。"): string {
  const raw = normalizeDisplayText(textValue(value)).trim();
  if (!raw || raw === "暂无预览内容") return "打开后查看详情。";
  if (looksLikeRawStructuredPayload(raw)) return fallback;
  const redacted = clampDisplayText(redactSensitiveText(raw).trim(), MAX_PREVIEW_TEXT_LENGTH);
  if (!redacted) return fallback;
  return redacted;
}

export function containsSensitiveDisplayText(value: unknown): boolean {
  const raw = textValue(value);
  return (
    matches(BEARER_TOKEN_PATTERN, raw) ||
    matches(TOKEN_VALUE_PATTERN, raw) ||
    matches(SECRET_ASSIGNMENT_PATTERN, raw) ||
    matches(JSON_SECRET_PATTERN, raw) ||
    matches(TOOL_ARGS_PATTERN, raw) ||
    matches(ENV_SECRET_ASSIGNMENT_PATTERN, raw) ||
    matches(URL_PATTERN, raw) ||
    matches(HOST_PATTERN, raw) ||
    matches(WINDOWS_PATH_PATTERN, raw) ||
    matches(UNC_PATH_PATTERN, raw) ||
    matches(POSIX_PATH_PATTERN, raw) ||
    matches(HOME_SHORTHAND_PATH_PATTERN, raw) ||
    matches(HOST_ASSIGNMENT_PATTERN, raw) ||
    matches(PRIVATE_KEY_PATTERN, raw) ||
    matches(CLOUD_TOKEN_PATTERN, raw) ||
    matches(LONG_SECRETISH_VALUE_PATTERN, raw) ||
    matches(EMAIL_PATTERN, raw) ||
    matches(RAW_STRUCTURED_PAYLOAD_PATTERN, raw)
  );
}

function redactSensitiveText(value: string): string {
  return value
    .replace(PRIVATE_KEY_PATTERN, "[已隐藏的私钥]")
    .replace(ANSI_ESCAPE_PATTERN, "")
    .replace(CONTROL_CHARACTER_PATTERN, " ")
    .replace(BEARER_TOKEN_PATTERN, "Bearer [已隐藏]")
    .replace(TOKEN_VALUE_PATTERN, "[已隐藏的 token]")
    .replace(JSON_SECRET_PATTERN, "$1\"[已隐藏]\"")
    .replace(SECRET_ASSIGNMENT_PATTERN, "$1=[已隐藏]")
    .replace(TOOL_ARGS_PATTERN, "[已隐藏的工具参数]")
    .replace(ENV_SECRET_ASSIGNMENT_PATTERN, "$1=[已隐藏]")
    .replace(URL_PATTERN, "[已隐藏的连接地址]")
    .replace(HOST_ASSIGNMENT_PATTERN, "$1=[已隐藏]")
    .replace(HOST_PATTERN, "[已隐藏的电脑地址]")
    .replace(WINDOWS_PATH_PATTERN, "[已隐藏的本机路径]")
    .replace(UNC_PATH_PATTERN, "[已隐藏的本机路径]")
    .replace(POSIX_PATH_PATTERN, "$1[已隐藏的本机路径]")
    .replace(HOME_SHORTHAND_PATH_PATTERN, "$1[已隐藏的本机路径]")
    .replace(CLOUD_TOKEN_PATTERN, "[已隐藏的访问凭据]")
    .replace(LONG_SECRETISH_VALUE_PATTERN, "[已隐藏的长凭据]")
    .replace(EMAIL_PATTERN, "[已隐藏的邮箱地址]");
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

function normalizeDisplayText(value: string): string {
  return value
    .replace(ANSI_ESCAPE_PATTERN, "")
    .replace(CONTROL_CHARACTER_PATTERN, " ")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");
}

function clampDisplayText(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength).trimEnd()}\n…内容较长，已在手机端截断。`;
}

function textValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}
