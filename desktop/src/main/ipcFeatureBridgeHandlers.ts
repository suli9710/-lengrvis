import { ipcMain } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type { BackendProcessManager } from "./backendProcess";
import { proxyExplicitDesktopBridgeRequest } from "./ipcBackendProxy";
import { confirmNativeDesktopAction, truncateForDialog } from "./ipcNativeConfirmation";
import {
  validateBridgeIdentifier,
  validateBridgePathValue,
  validateBrowserSessionRequest,
  validateCommerceLicenseActivateRequest,
  validateCommerceLicenseInstallRequest,
  validateCommercePolicyImportRequest,
  validateHardwareAccelerationSmokeRequest,
  validateMemoryRecallRequest,
  validateMemorySaveRequest,
  validateOptionalModelRequest,
  validatePlainBridgeBody,
  validateScheduleCreateRequest,
  validateScheduleEnableRequest
} from "./ipcValidation";
import { assertTrustedRenderer } from "./rendererTrust";

export function registerFeatureBridgeIpcHandlers(backend: BackendProcessManager): void {
  ipcMain.handle(IPC_CHANNELS.skillsImport, async (event, packagePath: unknown) => {
    assertTrustedRenderer(event);
    const safePackagePath = validateBridgePathValue(packagePath, "skill package path");
    // Skill import reads and registers code/tools from an arbitrary filesystem
    // path. Require an explicit native confirmation that shows the path so a
    // compromised renderer cannot silently import from an attacker-chosen location.
    await confirmNativeDesktopAction(event, {
      title: "Confirm skill import",
      message: "Import this skill package?",
      detail: `Path: ${safePackagePath}\n\nOnly import skill packages you trust. Skills can register tools and run packaged code under the app's permissions.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/skills/import",
      method: "POST",
      body: { path: safePackagePath }
    });
  });

  ipcMain.handle(IPC_CHANNELS.skillsRefresh, async (event) => {
    assertTrustedRenderer(event);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/skills/refresh",
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.localModelInstall, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateOptionalModelRequest(request, "local model install request");
    await confirmNativeDesktopAction(event, {
      title: "Confirm local model installation",
      message: "Install the local AI model?",
      detail: `Model: ${body.model ?? "recommended default"}\n\nThis may download a large package and use significant disk space.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/install-local-model",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.ollamaInstall, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm Ollama installation",
      message: "Install the local AI runtime?",
      detail: "This may download and install Ollama on this computer."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/ollama/install",
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.ollamaPull, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateOptionalModelRequest(request ?? {}, "Ollama pull request");
    await confirmNativeDesktopAction(event, {
      title: "Confirm model download",
      message: "Download this Ollama model?",
      detail: `Model: ${body.model ?? "recommended default"}\n\nModel downloads can use substantial network bandwidth and disk space.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/ollama/pull",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.ollamaStart, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm local AI start",
      message: "Start the Ollama service?",
      detail: "This starts a local background process that can load AI models and use system resources."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/ollama/start",
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.hardwareAccelerationSmoke, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateHardwareAccelerationSmokeRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm local model smoke test",
      message: "Run this local hardware acceleration test?",
      detail: `Operation: ${body.operation}\n\nThis may load local ONNX/OCR models and use CPU, GPU, disk, or memory resources.`
    });
    const endpointByOperation = {
      warmup: "/api/settings/onnx/warmup",
      test_generate: "/api/settings/onnx/test-generate",
      test_embedding: "/api/settings/onnx/test-embedding",
      test_ocr: "/api/settings/onnx/test-ocr",
      test_image_embedding: "/api/settings/onnx/test-image-embedding"
    } as const;
    const requestBody =
      body.operation === "test_generate"
        ? { prompt: body.prompt || undefined, max_tokens: body.maxTokens, model_path: body.modelPath }
        : body.operation === "test_embedding"
          ? { texts: body.texts.length ? body.texts : undefined, model_path: body.modelPath }
          : body.operation === "test_image_embedding"
            ? { image_path: body.imagePath, model_path: body.modelPath }
            : { model_path: body.modelPath };
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: endpointByOperation[body.operation],
      method: "POST",
      body: requestBody
    });
  });

  ipcMain.handle(IPC_CHANNELS.browserObserve, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const { sessionId } = validateBrowserSessionRequest(request);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/browser/observe",
      method: "POST",
      body: { session_id: sessionId }
    });
  });

  ipcMain.handle(IPC_CHANNELS.browserReplayExport, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const { sessionId } = validateBrowserSessionRequest(request);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/browser/replay-export",
      method: "POST",
      body: { session_id: sessionId }
    });
  });

  ipcMain.handle(IPC_CHANNELS.commerceLicenseInstall, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateCommerceLicenseInstallRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm license installation",
      message: "Install this commerce license?",
      detail: `License token length: ${body.token.length} characters\n\nOnly install license tokens from a trusted Lengrvis source. The token itself is not displayed here.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/commerce/license/install",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.commerceLicenseActivate, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateCommerceLicenseActivateRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm license activation",
      message: "Activate this commerce license?",
      detail: `App version: ${body.appVersion ?? "desktop"}\nActivation key length: ${body.activationKey.length} characters\n\nThis may contact the configured activation service.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/commerce/license/activate",
      method: "POST",
      body: {
        activation_key: body.activationKey,
        app_version: body.appVersion ?? "desktop"
      }
    });
  });

  ipcMain.handle(IPC_CHANNELS.commercePolicyImport, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateCommercePolicyImportRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm commerce policy import",
      message: "Import this commerce permission policy?",
      detail: `Rules: ${body.policy.rules?.length ?? 0}\n\nThis can change what tools the agent may use under a paid policy management entitlement.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/commerce/policy/import",
      method: "POST",
      query: body.confirmationNonce ? { confirmation_nonce: body.confirmationNonce } : undefined,
      body: { policy: body.policy }
    });
  });

  ipcMain.handle(IPC_CHANNELS.memoriesSave, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateMemorySaveRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm memory write",
      message: "Save this memory for future agent context?",
      detail: `Kind: ${body.kind ?? "fact"}\n\nMemory text:\n${truncateForDialog(body.content)}`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/memories",
      method: "POST",
      body: {
        content: body.content,
        tags: body.tags ?? [],
        task_id: body.taskId ?? "",
        kind: body.kind ?? "fact"
      }
    });
  });

  ipcMain.handle(IPC_CHANNELS.memoriesRecall, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateMemoryRecallRequest(request);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/memories/recall",
      method: "POST",
      body: {
        query: body.query,
        k: body.k ?? 5,
        tags: body.tags ?? []
      }
    });
  });

  ipcMain.handle(IPC_CHANNELS.memoriesForget, async (event, memoryId: unknown) => {
    assertTrustedRenderer(event);
    const safeMemoryId = validateBridgeIdentifier(memoryId, "memory id");
    await confirmNativeDesktopAction(event, {
      type: "warning",
      confirmLabel: "Forget memory",
      title: "Confirm memory deletion",
      message: "Forget this saved memory?",
      detail: `Memory id: ${safeMemoryId}`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/memories/${encodeURIComponent(safeMemoryId)}`,
      method: "DELETE"
    });
  });

  ipcMain.handle(IPC_CHANNELS.schedulesList, async (event) => {
    assertTrustedRenderer(event);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/schedules",
      method: "GET"
    });
  });

  ipcMain.handle(IPC_CHANNELS.schedulesCreate, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateScheduleCreateRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm scheduled agent run",
      message: "Create this schedule?",
      detail: [
        `Cron: ${body.cron}`,
        `Mode: ${body.mode}`,
        "",
        "Goal:",
        truncateForDialog(body.goal),
        "",
        "Scheduled runs can use tools and authorized files in the future."
      ].join("\n")
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/schedules",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.schedulesDelete, async (event, scheduleId: unknown) => {
    assertTrustedRenderer(event);
    const safeScheduleId = validateBridgeIdentifier(scheduleId, "schedule id");
    await confirmNativeDesktopAction(event, {
      title: "Confirm schedule deletion",
      message: "Delete this schedule?",
      detail: `Schedule id: ${safeScheduleId}`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/schedules/${encodeURIComponent(safeScheduleId)}`,
      method: "DELETE"
    });
  });

  ipcMain.handle(IPC_CHANNELS.schedulesEnable, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validatePlainBridgeBody(request, "schedule enable request");
    const { scheduleId, enabled } = validateScheduleEnableRequest(body.scheduleId, body.enabled);
    await confirmNativeDesktopAction(event, {
      title: enabled ? "Confirm schedule enable" : "Confirm schedule disable",
      message: enabled ? "Enable this schedule?" : "Disable this schedule?",
      detail: `Schedule id: ${scheduleId}`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/schedules/${encodeURIComponent(scheduleId)}/enable`,
      method: "POST",
      body: { enabled }
    });
  });
}
