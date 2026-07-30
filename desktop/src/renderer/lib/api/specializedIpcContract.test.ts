import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import { rollbackCleanupEndpoint } from "./cleanupClient";
import { installCommerceLicenseEndpoint } from "./commerceClient";
import { exportBrowserReplayEndpoint, observeBrowserSessionEndpoint } from "./browserClient";
import { parseDocumentEndpoint } from "./documentClient";
import { startRunEndpoint } from "./executionClient";
import { runHardwareAccelerationSmokeEndpoint } from "./hardwareAccelerationClient";
import { installOllamaEndpoint } from "./localModelClient";
import { forgetMemoryEndpoint } from "./memoryClient";
import { createMobilePairingCodeEndpoint } from "./mobilePairingClient";
import { listSchedulesEndpoint } from "./scheduleClient";
import { confirmPermissionRuleDeleteEndpoint } from "./settingsClient";
import { openWindowsSettingsEndpoint } from "./systemClient";

type EndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

describe("specialized IPC ApiResponse contract", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("sanitizes representative rejected adapters without falling back to HTTP", async () => {
    const sensitiveError = new Error("authorization=super-secret at C:\\Users\\Private\\bridge.log");
    const reject = () => Promise.reject(sensitiveError);
    (window as unknown as { lengrvis?: unknown }).lengrvis = {
      system: { openSettings: vi.fn(reject) },
      schedules: { list: vi.fn(reject) },
      memories: { forget: vi.fn(reject) },
      mobilePairing: { createCode: vi.fn(reject) },
      permissionPolicy: { confirmRelaxation: vi.fn(reject) },
      ollama: { install: vi.fn(reject) },
      documents: { parse: vi.fn(reject) },
      runs: { start: vi.fn(reject) },
      cleanup: { rollback: vi.fn(reject) },
      commerce: { installLicense: vi.fn(reject) },
      hardwareAcceleration: { smoke: vi.fn(reject) },
      browserBackend: { observe: vi.fn(reject), replayExport: vi.fn(reject) }
    };
    const fallbackRequest = vi.fn() as unknown as EndpointRequest;

    const responses = await Promise.all([
      openWindowsSettingsEndpoint(fallbackRequest, "ms-settings:display"),
      listSchedulesEndpoint(fallbackRequest),
      forgetMemoryEndpoint(fallbackRequest, "memory-one"),
      createMobilePairingCodeEndpoint(fallbackRequest),
      confirmPermissionRuleDeleteEndpoint(fallbackRequest, "rule-one"),
      installOllamaEndpoint(fallbackRequest),
      parseDocumentEndpoint(fallbackRequest, { path: "C:\\safe\\report.docx", includeText: true }),
      startRunEndpoint(fallbackRequest, { content: "Inspect downloads", mode: "efficiency" }),
      rollbackCleanupEndpoint(fallbackRequest, { planId: "plan-one", executionId: "execution-one" }),
      installCommerceLicenseEndpoint(fallbackRequest, "signed-license"),
      runHardwareAccelerationSmokeEndpoint(fallbackRequest, { operation: "warmup" }),
      observeBrowserSessionEndpoint(fallbackRequest, "session-one"),
      exportBrowserReplayEndpoint(fallbackRequest, "session-one")
    ]);

    expect(fallbackRequest).not.toHaveBeenCalled();
    for (const response of responses) {
      expect(response).toMatchObject({
        ok: false,
        status: 0,
        error: {
          code: "IPC_REQUEST_FAILED",
          message: "Lengrvis 桌面连接暂时不可用，请重启应用后再试。"
        }
      });
      expect(JSON.stringify(response)).not.toContain("super-secret");
      expect(JSON.stringify(response)).not.toContain("Users\\Private");
    }
  });
});
