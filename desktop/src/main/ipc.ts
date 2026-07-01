import type { BackendProcessManager } from "./backendProcess";
import { registerBackendIpcHandlers } from "./ipcBackendHandlers";
import { registerFeatureBridgeIpcHandlers } from "./ipcFeatureBridgeHandlers";
import { registerMobilePairingIpcHandlers } from "./ipcMobilePairingHandlers";
import { registerSystemSettingsIpcHandlers } from "./ipcSystemSettingsHandlers";
import { registerTaskBridgeIpcHandlers } from "./ipcTaskBridgeHandlers";
import { registerWindowPathIpcHandlers } from "./ipcWindowPathHandlers";

export { isSafeExternalUrl } from "./externalUrl";
export { buildRequestUrl } from "./ipcValidation";
export { confirmNativeDesktopAction } from "./ipcNativeConfirmation";
export { assertTrustedRenderer, isTrustedRendererUrl } from "./rendererTrust";

export function registerIpcHandlers(backend: BackendProcessManager): void {
  const context = {
    backend,
    documentPathGrants: new Set<string>(),
    revealPathGrants: new Set<string>()
  };

  registerBackendIpcHandlers(backend);
  registerWindowPathIpcHandlers(context);
  registerTaskBridgeIpcHandlers(backend);
  registerFeatureBridgeIpcHandlers(backend);
  registerSystemSettingsIpcHandlers(context);
  registerMobilePairingIpcHandlers(backend);
}
