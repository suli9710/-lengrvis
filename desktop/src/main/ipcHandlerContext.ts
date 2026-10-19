import type { BackendProcessManager } from "./backendProcess";
import type { IpcPathGrantStores } from "./ipcPathGrants";
import type { LocalPrivacyEraser } from "./ipcPrivacyErase";

export interface IpcHandlerContext extends IpcPathGrantStores {
  backend: BackendProcessManager;
  localPrivacyEraser?: LocalPrivacyEraser;
}
