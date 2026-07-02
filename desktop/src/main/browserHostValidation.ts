import type { BrowserAction, BrowserHostBounds } from "../shared/types";
import { assertBrowserHostUrlAllowed } from "./browserHostNetworkGuard";

const MIN_BROWSER_SIZE = 80;

export function normalizeBrowserHostId(value?: string): string | undefined {
  const trimmed = value?.trim();
  return trimmed || undefined;
}

export function normalizeBrowserHostUrl(value?: string): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;
  if (trimmed === "about:blank") return trimmed;
  const withProtocol = /^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? trimmed : `https://${trimmed}`;
  const parsed = new URL(withProtocol);
  if (!["https:", "http:"].includes(parsed.protocol)) {
    throw new Error("Only http and https URLs can be opened in Watch Mode");
  }
  assertBrowserHostUrlAllowed(parsed);
  return parsed.toString();
}

export function normalizeBrowserHostBounds(bounds: BrowserHostBounds): BrowserHostBounds {
  return {
    x: Math.max(0, Math.round(bounds.x)),
    y: Math.max(0, Math.round(bounds.y)),
    width: Math.max(MIN_BROWSER_SIZE, Math.round(bounds.width)),
    height: Math.max(MIN_BROWSER_SIZE, Math.round(bounds.height))
  };
}

export function browserHostTimestamp(): string {
  return new Date().toISOString();
}

export function browserHostErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Browser host action failed";
}

export function requireBrowserActionUrl(action: BrowserAction): string {
  const url = normalizeBrowserHostUrl(action.url);
  if (!url) throw new Error("Browser action requires a URL");
  return url;
}

export function requireBrowserActionSelector(action: BrowserAction): string {
  const selector = action.selector?.trim();
  if (!selector) throw new Error("Browser action requires a selector");
  return selector;
}
