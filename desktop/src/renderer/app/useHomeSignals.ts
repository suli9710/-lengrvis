import { useMemo } from "react";

import type { BackendStatus } from "../../shared/types";
import type { LocalLLMHealth } from "../../shared/localModelTypes";
import type { AppSettings } from "../../shared/settingsTypes";
import type { RealtimeConnectionStatus } from "../lib/apiClient";
import type { AssistantMode } from "../store";
import {
  buildHomeReadinessItems,
  buildHomeTrustItems,
  connectionStateFromBackendAndRealtime
} from "../appViewModel";

interface HomeSignalsOptions {
  backendStatus: BackendStatus;
  realtimeStatus: RealtimeConnectionStatus | null;
  mode: AssistantMode;
  localLlmHealth: LocalLLMHealth | null;
  settings: AppSettings;
}

export function useHomeSignals({
  backendStatus,
  realtimeStatus,
  mode,
  localLlmHealth,
  settings
}: HomeSignalsOptions) {
  const connectionState = connectionStateFromBackendAndRealtime(backendStatus, realtimeStatus);
  const homeReadinessItems = useMemo(
    () =>
      buildHomeReadinessItems({
        connectionState,
        realtimeStatus,
        mode,
        localLlmHealth,
        allowedDirectories: settings.allowedDirectories,
        workspaceRoot: settings.workspaceRoot
      }),
    [connectionState, localLlmHealth, mode, realtimeStatus, settings.allowedDirectories, settings.workspaceRoot]
  );
  const homeTrustItems = useMemo(
    () =>
      buildHomeTrustItems({
        mode,
        localLlmHealth,
        allowedDirectories: settings.allowedDirectories,
        workspaceRoot: settings.workspaceRoot,
        allowCloudContext: settings.allowCloudContext,
        allowFileContentUpload: settings.allowFileContentUpload
      }),
    [
      localLlmHealth,
      mode,
      settings.allowCloudContext,
      settings.allowFileContentUpload,
      settings.allowedDirectories,
      settings.workspaceRoot
    ]
  );

  return { connectionState, homeReadinessItems, homeTrustItems };
}
