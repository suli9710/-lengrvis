import type { Ref } from "react";

import type { AppSettings } from "../../../shared/settingsTypes";
import type { LocalLLMHealth, LocalModelSetupPlan } from "../../../shared/localModelTypes";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { LocalModelInstaller, PrivacyFlowHint, PrivacyReadinessPanel } from "./LocalModelInstaller";

interface PrivacyModeEntryProps {
  api: LengrvisApiClient;
  draft: AppSettings;
  privacyIntentId?: number;
  privacyEntryRef: Ref<HTMLDivElement>;
  effectiveLocalLlmHealth: LocalLLMHealth | null;
  detectedLocalLlmHealth: LocalLLMHealth | null;
  localModelSetupPlan: LocalModelSetupPlan | null;
  isCheckingLocalLlm: boolean;
  isSaving: boolean;
  onEnablePrivacy: () => void;
  onRefreshLocalLlmHealth: () => Promise<LocalLLMHealth | null>;
}

export function PrivacyModeEntry({
  api,
  draft,
  privacyIntentId,
  privacyEntryRef,
  effectiveLocalLlmHealth,
  detectedLocalLlmHealth,
  localModelSetupPlan,
  isCheckingLocalLlm,
  isSaving,
  onEnablePrivacy,
  onRefreshLocalLlmHealth
}: PrivacyModeEntryProps) {
  const className = privacyIntentId === undefined
    ? "settings-grid__full settings-privacy-anchor"
    : "settings-grid__full settings-privacy-anchor settings-privacy-anchor--intent";

  return (
    <div ref={privacyEntryRef} className={className} tabIndex={-1}>
      {privacyIntentId !== undefined ? <PrivacyFlowHint /> : null}
      {draft.mode === "privacy" || draft.mode === "hybrid" ? (
        <LocalModelInstaller
          api={api}
          apiBaseUrl={draft.apiBaseUrl}
          readiness={effectiveLocalLlmHealth?.readiness}
          health={effectiveLocalLlmHealth}
          setupPlan={localModelSetupPlan}
          mode={draft.mode}
          onHealthRefresh={onRefreshLocalLlmHealth}
        />
      ) : (
        <PrivacyReadinessPanel
          mode={draft.mode}
          health={detectedLocalLlmHealth}
          setupPlan={localModelSetupPlan}
          checking={isCheckingLocalLlm}
          onEnablePrivacy={onEnablePrivacy}
          onRefresh={() => void onRefreshLocalLlmHealth()}
          disabled={isSaving}
        />
      )}
    </div>
  );
}
