import type { ApiMethod } from "./desktopBridgeTypes";

export interface RendererApiRouteRule {
  method: ApiMethod;
  template: string;
}

export const RENDERER_API_ROUTE_ALLOWLIST = [
  { method: "GET", template: "/api/health" },
  { method: "GET", template: "/api/chat/messages" },
  { method: "POST", template: "/api/chat" },
  { method: "GET", template: "/api/chat/proactive-suggestions" },
  { method: "GET", template: "/api/skills" },
  { method: "GET", template: "/api/runs" },
  { method: "GET", template: "/api/runs/:runId/timeline" },
  { method: "GET", template: "/api/tasks" },
  { method: "GET", template: "/api/tasks/:taskId" },
  { method: "GET", template: "/api/tasks/:taskId/artifacts" },
  { method: "GET", template: "/api/tasks/:taskId/timeline" },
  { method: "GET", template: "/api/tasks/:taskId/agent-messages" },
  { method: "GET", template: "/api/tasks/:taskId/safety-reviews" },
  { method: "GET", template: "/api/tasks/:taskId/rollback-preview" },
  { method: "GET", template: "/api/tasks/:taskId/explain" },
  { method: "GET", template: "/api/approvals/pending" },
  { method: "GET", template: "/api/commands" },
  { method: "GET", template: "/api/files/search" },
  { method: "GET", template: "/api/library" },
  { method: "POST", template: "/api/files/cluster" },
  { method: "POST", template: "/api/files/cleanup/scan" },
  { method: "POST", template: "/api/files/cleanup/plan" },
  { method: "GET", template: "/api/settings" },
  { method: "GET", template: "/api/settings/permission-policy" },
  { method: "GET", template: "/api/settings/local-llm/health" },
  { method: "GET", template: "/api/settings/local-model/setup-plan" },
  { method: "GET", template: "/api/settings/ollama/status" },
  { method: "GET", template: "/api/settings/llm/health" },
  { method: "GET", template: "/api/settings/llm/profile" },
  { method: "GET", template: "/api/settings/llm/cost-summary" },
  { method: "GET", template: "/api/settings/onnx/status" },
  { method: "GET", template: "/api/context/usage" },
  { method: "GET", template: "/api/perception/voice/health" },
  { method: "POST", template: "/api/perception/voice/transcribe" },
  { method: "GET", template: "/api/metrics/local" },
  { method: "GET", template: "/api/audit" },
  { method: "GET", template: "/api/system/info" },
  { method: "GET", template: "/api/system/diagnostics" },
  { method: "GET", template: "/api/system/processes" },
  { method: "GET", template: "/api/system/startup-items" },
  { method: "GET", template: "/api/apps" },
  { method: "GET", template: "/api/commerce/plan" },
  { method: "GET", template: "/api/commerce/license" },
  { method: "GET", template: "/api/commerce/usage/quota" },
  { method: "GET", template: "/api/memories" },
  { method: "GET", template: "/api/browser/sessions" },
  { method: "GET", template: "/api/browser/session/:sessionId/events" }
] as const satisfies readonly RendererApiRouteRule[];

export function isRendererApiRouteAllowed(pathname: string, method: ApiMethod): boolean {
  return RENDERER_API_ROUTE_ALLOWLIST.some(
    (rule) => rule.method === method && rendererApiRouteTemplateMatches(pathname, rule.template)
  );
}

export function rendererApiRouteTemplateMatches(pathname: string, template: string): boolean {
  if (!pathname.startsWith("/") || pathname.includes("//")) {
    return false;
  }
  const pathSegments = pathname.split("/").filter(Boolean);
  const templateSegments = template.split("/").filter(Boolean);
  if (pathSegments.length !== templateSegments.length) {
    return false;
  }
  return templateSegments.every((segment, index) => {
    if (segment.startsWith(":")) {
      return pathSegments[index].length > 0;
    }
    return segment === pathSegments[index];
  });
}
