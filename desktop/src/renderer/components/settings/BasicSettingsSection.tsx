import type { Dispatch, SetStateAction } from "react";

import type { HardwareAccelerationStatusPayload } from "../../../shared/hardwareAccelerationTypes";
import type { LocalLLMHealth, LocalModelSetupPlan } from "../../../shared/localModelTypes";
import type { AppSettings } from "../../../shared/settingsTypes";
import { LocalLlmHealthNotice, ModelBoundaryProfile } from "./LocalModelSettings";
import { updateWorkspaceRoot } from "./SettingsPanelHelpers";
import {
  displayMode,
  modeDescription,
  PERMISSION_MODE_OPTIONS,
  permissionModeLabel
} from "./settingsDisplay";

interface BasicSettingsSectionProps {
  draft: AppSettings;
  setDraft: Dispatch<SetStateAction<AppSettings>>;
  isSaving: boolean;
  effectiveLocalLlmHealth: LocalLLMHealth | null;
  detectedLocalLlmHealth: LocalLLMHealth | null;
  localModelSetupPlan: LocalModelSetupPlan | null;
  hardwareStatus: HardwareAccelerationStatusPayload | null;
  privacyModeStatus: string;
  aiStatus: string;
  localAiStatus: string;
  realtimeStatusText: string;
  realtimeStatusProblem: boolean;
  onModeChange: (value: AppSettings["mode"]) => void;
}

export function BasicSettingsSection({
  draft,
  setDraft,
  isSaving,
  effectiveLocalLlmHealth,
  detectedLocalLlmHealth,
  localModelSetupPlan,
  hardwareStatus,
  privacyModeStatus,
  aiStatus,
  localAiStatus,
  realtimeStatusText,
  realtimeStatusProblem,
  onModeChange
}: BasicSettingsSectionProps) {
  return (
    <fieldset className="mcp-servers settings-grid__full">
      <legend>基础设置</legend>
      <p className="muted">这里是普通用户的统一配置入口；不需要手动编辑 .env 或 config.yaml。</p>
      <div className="settings-grid settings-grid--balanced">
        <label className="field settings-grid__full">
          <span>模式</span>
          <div className="mode-radio-row">
            {(["efficiency", "hybrid", "privacy"] as const).map((value) => (
              <label key={value} className="mode-radio">
                <input
                  type="radio"
                  name="lengrvis-mode"
                  value={value}
                  checked={draft.mode === value}
                  disabled={isSaving && value === "privacy"}
                  onChange={() => onModeChange(value)}
                />
                <span>
                  <strong>{displayMode(value)}</strong>
                  <small>{modeDescription(value)}</small>
                </span>
              </label>
            ))}
          </div>
          {draft.mode === "privacy" || draft.mode === "hybrid" ? (
            <LocalLlmHealthNotice health={effectiveLocalLlmHealth} />
          ) : null}
          {privacyModeStatus ? (
            <small className="settings-status settings-status--error" role="status">
              {privacyModeStatus}
            </small>
          ) : null}
          <ModelBoundaryProfile
            mode={draft.mode}
            allowCloudContext={draft.allowCloudContext}
            allowFileContentUpload={draft.allowFileContentUpload}
            localReady={Boolean(detectedLocalLlmHealth?.available || localModelSetupPlan?.ready)}
            localHealth={detectedLocalLlmHealth}
            setupPlan={localModelSetupPlan}
            hardwareStatus={hardwareStatus}
            cloudModel={draft.model}
          />
        </label>
        <label className="field">
          <span>工作区文件夹</span>
          <input
            value={draft.workspaceRoot}
            onChange={(event) => setDraft((current) => updateWorkspaceRoot(current, event.target.value))}
          />
          {(draft.allowedDirectories?.length ?? 0) > 1 ? (
            <small className="muted">
              已保留 {Number(draft.allowedDirectories?.length ?? 1) - 1} 个额外授权文件夹。
            </small>
          ) : null}
        </label>
        <label className="field settings-grid__full">
          <span>权限模式：{permissionModeLabel(draft.permissionMode)}</span>
          <div className="mode-radio-row permission-mode-row">
            {PERMISSION_MODE_OPTIONS.map((option) => (
              <label key={option.value} className="mode-radio">
                <input
                  type="radio"
                  name="lengrvis-permission-mode"
                  value={option.value}
                  checked={draft.permissionMode === option.value}
                  onChange={() => setDraft((current) => ({ ...current, permissionMode: option.value }))}
                />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
              </label>
            ))}
          </div>
        </label>
      </div>
      <div className="toggle-list">
        <label>
          <input
            type="checkbox"
            checked={draft.allowBrowserNetwork}
            onChange={(event) =>
              setDraft((current) => ({ ...current, allowBrowserNetwork: event.target.checked }))
            }
          />
          <span>允许访问网络</span>
        </label>
        <label>
          <input
            type="checkbox"
            checked={draft.allowCloudContext}
            onChange={(event) =>
              setDraft((current) => ({ ...current, allowCloudContext: event.target.checked }))
            }
          />
          <span>允许云端辅助</span>
        </label>
        <label>
          <input
            type="checkbox"
            checked={draft.allowFileContentUpload}
            onChange={(event) =>
              setDraft((current) => ({ ...current, allowFileContentUpload: event.target.checked }))
            }
          />
          <span>必要时允许读取文件内容</span>
        </label>
        <label>
          <input
            type="checkbox"
            checked={draft.remoteDesktopEnabled}
            onChange={(event) =>
              setDraft((current) => ({ ...current, remoteDesktopEnabled: event.target.checked }))
            }
          />
          <span>允许手机查看电脑屏幕</span>
        </label>
      </div>
      <div className="settings-status-grid">
        <p className="muted">Lengrvis: {aiStatus}</p>
        <p className="muted">本地 AI: {localAiStatus}</p>
        {realtimeStatusText ? (
          <p className={realtimeStatusProblem ? "settings-status settings-status--error" : "muted"}>
            实时通道：{realtimeStatusText}
          </p>
        ) : null}
      </div>
    </fieldset>
  );
}
