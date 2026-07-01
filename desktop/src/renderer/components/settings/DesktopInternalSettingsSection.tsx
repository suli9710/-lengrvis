import { lazy, Suspense, type Dispatch, type SetStateAction } from "react";

import type {
  AppSettings,
  HardwareAccelerationSmokePayload,
  HardwareAccelerationStatusPayload
} from "../../../shared/types";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { runtimeToProvider } from "./SettingsPanelHelpers";
import { DesktopInternalAppBrowserSettings } from "./DesktopInternalAppBrowserSettings";
import { DesktopInternalEmbeddingOcrSettings } from "./DesktopInternalEmbeddingOcrSettings";
import { DesktopInternalOnnxRuntimeSettings } from "./DesktopInternalOnnxRuntimeSettings";

const HardwareAccelerationCard = lazy(() =>
  import("./HardwareAccelerationCard").then((module) => ({ default: module.HardwareAccelerationCard }))
);

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

interface DesktopInternalSettingsSectionProps {
  api: LengrvisApiClient;
  draft: AppSettings;
  setDraft: SetDraft;
  hardwareStatus: HardwareAccelerationStatusPayload | null;
  isCheckingHardware: boolean;
  hardwareStatusError: string;
  hardwareSmokeStatus: string;
  hardwareSmoke: HardwareAccelerationSmokePayload | null;
  hardwareRuntime: string;
  onHardwareSmokeStatusChange: Dispatch<SetStateAction<string>>;
  onHardwareSmokeChange: Dispatch<SetStateAction<HardwareAccelerationSmokePayload | null>>;
}

export function DesktopInternalSettingsSection({
  api,
  draft,
  setDraft,
  hardwareStatus,
  isCheckingHardware,
  hardwareStatusError,
  hardwareSmokeStatus,
  hardwareSmoke,
  hardwareRuntime,
  onHardwareSmokeStatusChange,
  onHardwareSmokeChange
}: DesktopInternalSettingsSectionProps) {
  return (
    <fieldset className="mcp-servers">
      <legend>桌面端内部设置</legend>
      <div className="settings-grid settings-grid--balanced">
        <DesktopInternalAppBrowserSettings draft={draft} setDraft={setDraft} />
        <DesktopInternalOnnxRuntimeSettings draft={draft} setDraft={setDraft} />
        <DesktopInternalEmbeddingOcrSettings draft={draft} setDraft={setDraft} />
      </div>
      <Suspense fallback={<div className="hardware-acceleration">正在加载硬件加速设置...</div>}>
        <HardwareAccelerationCard
          api={api}
          settings={draft}
          status={hardwareStatus}
          loading={isCheckingHardware}
          error={hardwareStatusError}
          smokeStatus={hardwareSmokeStatus}
          smoke={hardwareSmoke}
          runtime={hardwareRuntime}
          onRuntimeChange={(value) =>
            setDraft((current) => ({
              ...current,
              onnxExecutionProvider: runtimeToProvider(value)
            }))
          }
          onSmokeStatusChange={onHardwareSmokeStatusChange}
          onSmokeChange={onHardwareSmokeChange}
        />
      </Suspense>
    </fieldset>
  );
}
