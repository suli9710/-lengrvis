import type { BackendProcessManager } from "./backendProcess";
import { registerBackendIpcHandlers } from "./ipcBackendHandlers";
import { registerButlerIpcHandlers } from "./ipcButlerHandlers";
import { registerFeatureBridgeIpcHandlers } from "./ipcFeatureBridgeHandlers";
import { registerMobilePairingIpcHandlers } from "./ipcMobilePairingHandlers";
import { registerSystemSettingsIpcHandlers } from "./ipcSystemSettingsHandlers";
import { registerTaskBridgeIpcHandlers } from "./ipcTaskBridgeHandlers";
import { registerWindowPathIpcHandlers } from "./ipcWindowPathHandlers";
import type { LocalPrivacyEraser } from "./ipcPrivacyErase";

export { isSafeExternalUrl } from "./externalUrl";
export { buildRequestUrl } from "./ipcValidation";
export { confirmNativeDesktopAction } from "./ipcNativeConfirmation";
export { assertTrustedRenderer, isTrustedRendererUrl } from "./rendererTrust";

export function registerIpcHandlers(
  backend: BackendProcessManager,
  localPrivacyEraser?: LocalPrivacyEraser
): void {
  const context = {
    backend,
    localPrivacyEraser,
    documentPathGrants: new Set<string>(),
    revealPathGrants: new Set<string>()
  };

  registerBackendIpcHandlers(backend);
  registerButlerIpcHandlers(backend);
  registerWindowPathIpcHandlers(context);
  registerTaskBridgeIpcHandlers(backend);
  registerFeatureBridgeIpcHandlers(backend);
  registerSystemSettingsIpcHandlers(context);
  registerMobilePairingIpcHandlers(backend);
}
