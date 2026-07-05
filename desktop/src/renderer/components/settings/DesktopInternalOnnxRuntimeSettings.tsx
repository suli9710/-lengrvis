import type { Dispatch, SetStateAction } from "react";

import type { AppSettings } from "../../../shared/settingsTypes";
import { normalizeHardwareRuntime } from "./SettingsPanelHelpers";

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

export function DesktopInternalOnnxRuntimeSettings({ draft, setDraft }: { draft: AppSettings; setDraft: SetDraft }) {
  return (
    <>
      <label className="field">
        <span>ONNX 模型路径</span>
        <input
          value={draft.onnxModelPath}
          onChange={(event) => setDraft((current) => ({ ...current, onnxModelPath: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>ONNX 运行提供方</span>
        <select
          value={draft.onnxExecutionProvider}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              onnxExecutionProvider: normalizeHardwareRuntime(event.target.value)
            }))
          }
        >
          <option value="">自动</option>
          <option value="WinML">WinML</option>
          <option value="DirectML">DirectML</option>
          <option value="OpenVINO">OpenVINO</option>
          <option value="CPU">CPU</option>
        </select>
      </label>
      <label className="field">
        <span>ONNX 提供方优先级</span>
        <input
          value={draft.onnxProviderPreference}
          onChange={(event) => setDraft((current) => ({ ...current, onnxProviderPreference: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>WinML / DirectML 设备 ID</span>
        <input
          value={draft.onnxDirectmlDeviceId}
          onChange={(event) => setDraft((current) => ({ ...current, onnxDirectmlDeviceId: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>OpenVINO 设备</span>
        <input
          value={draft.onnxOpenvinoDevice}
          onChange={(event) => setDraft((current) => ({ ...current, onnxOpenvinoDevice: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>OpenVINO 缓存目录</span>
        <input
          value={draft.onnxOpenvinoCacheDir}
          onChange={(event) => setDraft((current) => ({ ...current, onnxOpenvinoCacheDir: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>启动时预热</span>
        <select
          value={draft.onnxWarmOnStartup ? "yes" : "no"}
          onChange={(event) => setDraft((current) => ({ ...current, onnxWarmOnStartup: event.target.value === "yes" }))}
        >
          <option value="no">否</option>
          <option value="yes">是</option>
        </select>
      </label>
      <label className="field">
        <span>模型家族</span>
        <input
          value={draft.onnxModelFamily}
          onChange={(event) => setDraft((current) => ({ ...current, onnxModelFamily: event.target.value }))}
        />
      </label>
    </>
  );
}
