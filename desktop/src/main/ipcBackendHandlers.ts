import { ipcMain } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type { ApiRequest } from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import {
  ensureBackendReadyForRendererSubmission,
  isRendererTaskSubmissionRequest,
  proxyRendererApiRequest
} from "./ipcBackendProxy";
import { abortInflightApiGroup } from "./ipcInflight";
import { confirmNativeDesktopAction } from "./ipcNativeConfirmation";
import { validateApiAbortGroup } from "./ipcValidation";
import { assertTrustedRenderer } from "./rendererTrust";

export function registerBackendIpcHandlers(backend: BackendProcessManager): void {
  ipcMain.handle(IPC_CHANNELS.backendStatus, (event) => {
    assertTrustedRenderer(event);
    return backend.getStatus();
  });
  ipcMain.handle(IPC_CHANNELS.backendStart, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm backend start",
      message: "Start the Lengrvis backend service?",
      detail: "This starts the local agent service and makes configured tools available to the desktop app."
    });
    return backend.start();
  });
  ipcMain.handle(IPC_CHANNELS.backendStop, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm backend stop",
      message: "Stop the Lengrvis backend service?",
      detail: "Active tasks and desktop integrations may be interrupted."
    });
    return backend.stop();
  });
  ipcMain.handle(IPC_CHANNELS.backendForeground, (event) => {
    assertTrustedRenderer(event);
    return backend.enterForeground();
  });
  ipcMain.handle(IPC_CHANNELS.backendBackground, (event) => {
    assertTrustedRenderer(event);
    return backend.enterBackground();
  });

  ipcMain.handle(IPC_CHANNELS.apiRequest, async (event, request: ApiRequest) => {
    assertTrustedRenderer(event);
    if (isRendererTaskSubmissionRequest(request)) {
      const backendNotReady = await ensureBackendReadyForRendererSubmission(backend);
      if (backendNotReady) {
        return backendNotReady;
      }
    }
    return proxyRendererApiRequest(backend, request);
  });

  ipcMain.handle(IPC_CHANNELS.apiAbortInflight, async (event, abortGroup: unknown) => {
    assertTrustedRenderer(event);
    abortInflightApiGroup(validateApiAbortGroup(abortGroup));
  });
}
