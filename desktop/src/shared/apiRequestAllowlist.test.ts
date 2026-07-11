import { describe, expect, it } from "vitest";

import {
  isRendererApiRouteAllowed,
  rendererApiRouteTemplateMatches
} from "./apiRequestAllowlist";

describe("renderer API route allowlist", () => {
  it("allows only the declared method for exact routes", () => {
    expect(isRendererApiRouteAllowed("/api/health", "GET")).toBe(true);
    expect(isRendererApiRouteAllowed("/api/chat", "POST")).toBe(true);
    expect(isRendererApiRouteAllowed("/api/chat", "GET")).toBe(false);
  });

  it("matches one non-empty path segment per dynamic template parameter", () => {
    expect(rendererApiRouteTemplateMatches("/api/tasks/task-1/timeline", "/api/tasks/:taskId/timeline")).toBe(true);
    expect(rendererApiRouteTemplateMatches("/api/tasks/task-1/private/timeline", "/api/tasks/:taskId/timeline")).toBe(false);
    expect(isRendererApiRouteAllowed("/api/browser/session/session-1/events", "GET")).toBe(true);
  });

  it("denies unknown routes by default, including future sensitive-looking routes", () => {
    expect(isRendererApiRouteAllowed("/api/credentials/export", "GET")).toBe(false);
    expect(isRendererApiRouteAllowed("/api/credentials/export", "POST")).toBe(false);
    expect(isRendererApiRouteAllowed("/api/tasks/task-1/cancel", "POST")).toBe(false);
  });
});
