import type {
  BrowserAction,
  BrowserActivityEvent,
  BrowserHostActionResult,
  BrowserHostSnapshot,
  BrowserSession
} from "./browserTypes";

const SENSITIVE_QUERY_KEY_NAMES = [
  "access_token",
  "api_key",
  "apikey",
  "auth",
  "auth_token",
  "authorization",
  "client_secret",
  "code",
  "cookie",
  "id_token",
  "jwt",
  "key",
  "oauth_token",
  "password",
  "refresh_token",
  "secret",
  "session",
  "session_id",
  "token"
] as const;

const SENSITIVE_QUERY_KEYS = new Set<string>(SENSITIVE_QUERY_KEY_NAMES);
const SENSITIVE_URL_PARAM_REGEX = new RegExp(`([?&#](?:${SENSITIVE_QUERY_KEY_NAMES.join("|")})=)[^&#\\s"'<>]+`, "gi");
const URL_IN_TEXT_REGEX = /\b(?:https?:\/\/|file:\/\/|app:\/\/)[^\s"'<>]+/gi;

export function sanitizeSessionForRenderer(session: BrowserSession): BrowserSession {
  return {
    ...session,
    current_url: redactUrl(session.current_url),
    last_observation: sanitizeObservationForRenderer(session.last_observation)
  };
}

export function sanitizeEventForRenderer(event: BrowserActivityEvent): BrowserActivityEvent {
  return {
    ...event,
    action: sanitizeActionForRenderer(event.action),
    url: redactUrl(event.url),
    screenshot_url: event.screenshot_url ? "[redacted:screenshot]" : undefined,
    error: event.error ? redactSensitiveText(event.error) : undefined
  };
}

export function sanitizeActionForRenderer(action: BrowserAction | undefined): BrowserAction | undefined {
  if (!action) return undefined;
  const sanitized: BrowserAction = { ...action };
  if (typeof sanitized.url === "string") {
    sanitized.url = redactUrl(sanitized.url);
  }
  if (typeof sanitized.selector === "string") {
    sanitized.selector = "[redacted]";
  }
  if (typeof sanitized.text === "string") {
    sanitized.text = "[redacted]";
  }
  if (sanitized.fields && Object.keys(sanitized.fields).length) {
    sanitized.fields = Object.fromEntries(
      Object.entries(sanitized.fields).map(([key, value], index) => [
        `field_${index + 1}`,
        typeof value === "string" ? "[redacted]" : value
      ])
    ) as Record<string, string>;
  }
  return sanitized;
}

export function sanitizeActionResultForRenderer(result: BrowserHostActionResult): BrowserHostActionResult {
  return {
    ...result,
    session: result.session ? sanitizeSessionForRenderer(result.session) : undefined,
    event: result.event ? sanitizeEventForRenderer(result.event) : undefined,
    snapshot: result.snapshot ? sanitizeSnapshotForRenderer(result.snapshot) : undefined,
    error: result.error ? redactSensitiveText(result.error) : undefined
  };
}

export function sanitizeSnapshotForRenderer(snapshot: BrowserHostSnapshot): BrowserHostSnapshot {
  return {
    ...snapshot,
    sessions: snapshot.sessions.map(sanitizeSessionForRenderer),
    events: snapshot.events.map(sanitizeEventForRenderer)
  };
}

export function sanitizeObservationForRenderer(value: BrowserSession["last_observation"]): BrowserSession["last_observation"] {
  if (typeof value === "string") {
    return value;
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  return sanitizeRecordForRenderer(value);
}

export function sanitizeRecordForRenderer(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      const lowered = key.toLowerCase();
      if (typeof item === "string") {
        if (lowered === "url" || lowered.endsWith("_url") || lowered === "href") {
          return [key, redactUrl(item)];
        }
        if (lowered === "text" || lowered.endsWith("_text")) {
          return [key, item ? "[redacted:text]" : ""];
        }
        if (SENSITIVE_QUERY_KEYS.has(lowered)) {
          return [key, "[redacted]"];
        }
        return [key, redactSensitiveText(item)];
      }
      if (Array.isArray(item)) {
        return [
          key,
          item.map((child) =>
            child && typeof child === "object" ? sanitizeRecordForRenderer(child as Record<string, unknown>) : child
          )
        ];
      }
      if (item && typeof item === "object") {
        return [key, sanitizeRecordForRenderer(item as Record<string, unknown>)];
      }
      return [key, item];
    })
  );
}

export function redactUrl(value: string): string;
export function redactUrl(value: undefined): undefined;
export function redactUrl(value: string | undefined): string | undefined;
export function redactUrl(value: string | undefined): string | undefined {
  if (!value) return value;
  try {
    const parsed = new URL(value);
    for (const key of [...parsed.searchParams.keys()]) {
      if (SENSITIVE_QUERY_KEYS.has(key.toLowerCase())) {
        parsed.searchParams.set(key, "[redacted]");
      }
    }
    parsed.hash = redactUrlFragment(parsed.hash);
    if (parsed.username) parsed.username = "[redacted]";
    if (parsed.password) parsed.password = "[redacted]";
    return parsed.toString();
  } catch {
    return value.replace(SENSITIVE_URL_PARAM_REGEX, "$1[redacted]");
  }
}

function redactUrlFragment(hash: string): string {
  if (!hash) return hash;
  return hash.replace(SENSITIVE_URL_PARAM_REGEX, "$1[redacted]");
}

export function redactSensitiveText(value: string): string {
  return value
    .replace(URL_IN_TEXT_REGEX, (match) => redactUrl(match))
    .replace(/\b[a-z][\w-]*\[[^\]]*(?:password|token|secret|cookie|session|auth|key)[^\]]*\]/gi, "[redacted]")
    .replace(/\[[^\]]*(?:password|token|secret|cookie|session|auth|key)[^\]]*\]/gi, "[redacted]")
    .replace(/#[A-Za-z0-9_-]*(?:password|token|secret|cookie|session|auth|key)[A-Za-z0-9_-]*/gi, "#[redacted]")
    .replace(/\b(?:token|password|secret|api[_-]?key|authorization|cookie|session|jwt|oauth)[\w.-]*\s*[:=]\s*[^\s"'<>]+/gi, (match) =>
      match.replace(/([:=]\s*)[^\s"'<>]+/, "$1[redacted]")
    )
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b/gi, "Bearer [redacted]")
    .replace(/\bsk-[A-Za-z0-9_-]{8,}\b/g, "sk-[redacted]");
}
