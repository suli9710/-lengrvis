import type { BackendProcessManager } from "./backendProcess";
import type { IpcPathGrantStores } from "./ipcPathGrants";

export interface IpcHandlerContext extends IpcPathGrantStores {
  backend: BackendProcessManager;
  localPrivacyEraser?: {
    eraseLocalPrivateData: () => Promise<void>;
  };
}
