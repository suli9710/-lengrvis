import { Save } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

import type {
  AppSettings,
  BackendStatus,
  LLMCostSummary,
  LLMHealthStatus,
  HardwareAccelerationStatusPayload,
  HardwareAccelerationSmokePayload,
  LocalLLMHealth,
  LocalModelSetupPlan,
} from "../../shared/types";
import { buildMobilePairingQrContent } from "../../shared/mobilePairingPayload";
import {
  type LengrvisApiClient,
  type MobileDevice,
  type MobilePairingCode,
  type RealtimeConnectionStatus,
  type RemoteInputGrant
} from "../lib/apiClient";
import { motionAwareScrollBehavior } from "../lib/motion";
import { zhRealtimeConnectionStatus, zhRealtimeShortStatus } from "../lib/zh";
import { Badge, Panel } from "./Panel";
import {
  AiConnectionSection,
  DesktopInternalSettingsSection,
  GenerationStabilitySection,
  McpServersSection,
  RuntimeControlSection
} from "./settings/AdvancedSettingsSections";
import { BasicSettingsSection } from "./settings/BasicSettingsSection";
import {
  buildPermissionRule,
  DEFAULT_PERMISSION_POLICY,
  DEFAULT_PERMISSION_RULE_DRAFT,
  mapPermissionPolicy,
  PermissionPolicyEditor,
  type BackendPermissionPolicy,
  type PermissionPolicy
} from "./settings/PermissionPolicyEditor";
import { MobilePairingSection } from "./settings/MobilePairingSection";
import { PrivacyModeEntry } from "./settings/PrivacyModeEntry";
import { appStatusLabel } from "./settings/settingsDisplay";
import {
  providerToRuntime,
  readableError
} from "./settings/SettingsPanelHelpers";

const REMOTE_INPUT_GRANT_TTL_SECONDS = 5 * 60;

interface SettingsPanelProps {
  settings: AppSettings;
  backendStatus: BackendStatus;
  realtimeStatus?: RealtimeConnectionStatus | null;
  localLlmHealth: LocalLLMHealth | null;
  llmHealth: LLMHealthStatus | null;
  llmCostSummary: LLMCostSummary | null;
  hardwareAccelerationStatus?: HardwareAccelerationStatusPayload | null;
  onSave: (settings: AppSettings) => Promise<void>;
  onLocalLlmHealthChange?: (health: LocalLLMHealth | null) => void;
  onStartBackend: () => Promise<void>;
  onStopBackend: () => Promise<void>;
  api: LengrvisApiClient;
  privacyIntentId?: number;
}

const CommercePanel = lazy(() =>
  import("./settings/CommercePanel").then((module) => ({ default: module.CommercePanel }))
);
const PrivacyDataPanel = lazy(() =>
  import("./settings/PrivacyDataPanel").then((module) => ({ default: module.PrivacyDataPanel }))
);

export function SettingsPanel({
  settings,
  backendStatus,
  realtimeStatus,
  localLlmHealth,
  llmHealth,
  llmCostSummary,
  hardwareAccelerationStatus,
  onSave,
  onLocalLlmHealthChange,
  onStartBackend,
  onStopBackend,
  api,
  privacyIntentId
}: SettingsPanelProps) {
  const [draft, setDraft] = useState(settings);
  const [isSaving, setIsSaving] = useState(false);
  const [pairing, setPairing] = useState<MobilePairingCode | null>(null);
  const [pairingError, setPairingError] = useState("");
  const [pairingCopyStatus, setPairingCopyStatus] = useState("");
  const [isPairing, setIsPairing] = useState(false);
  const [pairedDevices, setPairedDevices] = useState<MobileDevice[]>([]);
  const [revokingDeviceId, setRevokingDeviceId] = useState("");
  const [remoteInputGrantingDeviceId, setRemoteInputGrantingDeviceId] = useState("");
  const [remoteInputRevokingGrantId, setRemoteInputRevokingGrantId] = useState("");
  const [permissionPolicy, setPermissionPolicy] = useState<PermissionPolicy>(DEFAULT_PERMISSION_POLICY);
  const [permissionDraft, setPermissionDraft] = useState(DEFAULT_PERMISSION_RULE_DRAFT);
  const [permissionStatus, setPermissionStatus] = useState("");
  const [isPermissionSaving, setIsPermissionSaving] = useState(false);
  const [detectedLocalLlmHealth, setDetectedLocalLlmHealth] = useState<LocalLLMHealth | null>(localLlmHealth);
  const [isCheckingLocalLlm, setIsCheckingLocalLlm] = useState(false);
  const [localModelSetupPlan, setLocalModelSetupPlan] = useState<LocalModelSetupPlan | null>(null);
  const [hardwareStatus, setHardwareStatus] = useState<HardwareAccelerationStatusPayload | null>(hardwareAccelerationStatus ?? null);
  const [isCheckingHardware, setIsCheckingHardware] = useState(false);
  const [hardwareStatusError, setHardwareStatusError] = useState("");
  const [hardwareSmokeStatus, setHardwareSmokeStatus] = useState("");
  const [hardwareSmoke, setHardwareSmoke] = useState<HardwareAccelerationSmokePayload | null>(null);
  const [saveError, setSaveError] = useState("");
  const [privacyModeStatus, setPrivacyModeStatus] = useState("");
  const privacyEntryRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setDraft(settings);
  }, [settings]);

  useEffect(() => {
    setDetectedLocalLlmHealth(localLlmHealth);
  }, [localLlmHealth]);

  useEffect(() => {
    setHardwareStatus(hardwareAccelerationStatus ?? null);
  }, [hardwareAccelerationStatus]);

  useEffect(() => {
    if (privacyIntentId === undefined) return;
    window.setTimeout(() => {
      privacyEntryRef.current?.scrollIntoView({ behavior: motionAwareScrollBehavior(), block: "start" });
      privacyEntryRef.current?.focus({ preventScroll: true });
    }, 0);
  }, [privacyIntentId]);

  useEffect(() => {
    let cancelled = false;
    void api.getLocalModelSetupPlan()
      .then((response) => {
        if (!cancelled && response.ok && response.data) {
          setLocalModelSetupPlan(response.data);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [api]);

  const refreshLocalLlmHealth = useCallback(async (): Promise<LocalLLMHealth | null> => {
    setIsCheckingLocalLlm(true);
    try {
      const [response, setupPlanResponse] = await Promise.all([
        api.getLocalLlmHealth(),
        api.getLocalModelSetupPlan().catch(() => null)
      ]);
      const nextHealth = response.ok && response.data
        ? response.data
        : {
            available: false,
            selectedBackend: null,
            probeOrder: ["onnx", "ollama", "lmstudio", "llamacpp"],
            error: response.error?.message ?? "无法检查本地 AI。"
          };
      setDetectedLocalLlmHealth(nextHealth);
      setLocalModelSetupPlan(setupPlanResponse?.ok && setupPlanResponse.data ? setupPlanResponse.data : null);
      onLocalLlmHealthChange?.(nextHealth);
      return nextHealth;
    } finally {
      setIsCheckingLocalLlm(false);
    }
  }, [api, onLocalLlmHealthChange]);

  useEffect(() => {
    if (draft.mode === "efficiency" || detectedLocalLlmHealth) return;
    let cancelled = false;
    setIsCheckingLocalLlm(true);
    void Promise.all([
      api.getLocalLlmHealth(),
      api.getLocalModelSetupPlan().catch(() => null)
    ]).then(([response, setupPlanResponse]) => {
      if (cancelled) return;
      if (response.ok && response.data) {
        setDetectedLocalLlmHealth(response.data);
        onLocalLlmHealthChange?.(response.data);
      } else {
        const fallbackHealth: LocalLLMHealth = {
          available: false,
          selectedBackend: null,
          probeOrder: ["onnx", "ollama", "lmstudio", "llamacpp"],
          error: response.error?.message ?? "无法检查本地 AI。"
        };
        setDetectedLocalLlmHealth(fallbackHealth);
        onLocalLlmHealthChange?.(fallbackHealth);
      }
      setLocalModelSetupPlan(setupPlanResponse?.ok && setupPlanResponse.data ? setupPlanResponse.data : null);
    }).finally(() => {
      if (!cancelled) setIsCheckingLocalLlm(false);
    });
    return () => {
      cancelled = true;
    };
  }, [api, detectedLocalLlmHealth, draft.mode, onLocalLlmHealthChange]);

  useEffect(() => {
    let cancelled = false;
    setIsCheckingHardware(true);
    void api.getHardwareAccelerationStatus().then((response) => {
      if (cancelled) return;
      if (response.ok && response.data) {
        setHardwareStatus(response.data);
        setHardwareStatusError("");
      } else {
        setHardwareStatus({
          available: false,
          kind: "onnx",
          modelPath: draft.onnxModelPath,
          executionProvider: "",
          availableProviders: [],
          generationRuntime: "",
          error: response.error?.message ?? "无法检查硬件加速。"
        });
        setHardwareStatusError(response.error?.message ?? "无法检查硬件加速。");
      }
    }).finally(() => {
      if (!cancelled) setIsCheckingHardware(false);
    });
    return () => {
      cancelled = true;
    };
  }, [api, draft.onnxModelPath, draft.onnxExecutionProvider]);

  const save = async () => {
    setIsSaving(true);
    setSaveError("");
    setPrivacyModeStatus("");
    try {
      await onSave(draft);
    } catch (error) {
      setSaveError(readableError(error, "无法保存设置"));
    } finally {
      setIsSaving(false);
    }
  };

  const createPairingCode = async () => {
    setIsPairing(true);
    setPairingError("");
    setPairingCopyStatus("");
    try {
      const response = await api.createMobilePairingCode();
      if (response.ok && response.data) {
        setPairing(response.data);
        void refreshPairedDevices();
      } else {
        setPairingError(response.error?.message ?? "无法生成配对码");
      }
    } catch (error) {
      setPairingError(readableError(error, "无法生成配对码"));
    } finally {
      setIsPairing(false);
    }
  };

  const copyPairingPayload = async () => {
    if (!pairing) return;
    const payload = buildMobilePairingQrContent(pairing).value;
    if (!navigator.clipboard?.writeText) {
      setPairingCopyStatus("剪贴板不可用，请打开手机 App 扫码。");
      return;
    }
    try {
      await navigator.clipboard.writeText(payload);
      setPairingCopyStatus("已复制备用配对信息；优先使用二维码扫码。");
    } catch {
      setPairingCopyStatus("复制失败，请打开手机 App 扫码。");
    }
  };

  const refreshPairedDevices = useCallback(async () => {
    const response = await api.listMobileDevices();
    if (response.ok && response.data) {
      setPairedDevices(response.data.devices);
    }
  }, [api]);

  const revokePairedDevice = async (device: MobileDevice) => {
    const deviceId = device.device_id;
    if (!deviceId || revokingDeviceId) return;
    setRevokingDeviceId(deviceId);
    setPairingError("");
    try {
      const response = await api.revokeMobileDevice(deviceId);
      if (response.ok) {
        await refreshPairedDevices();
      } else {
        setPairingError(response.error?.message ?? "无法断开这台手机。");
      }
    } catch (error) {
      setPairingError(readableError(error, "无法断开这台手机。"));
    } finally {
      setRevokingDeviceId("");
    }
  };

  const createRemoteInputGrant = async (device: MobileDevice) => {
    const deviceId = device.device_id;
    if (!deviceId || remoteInputGrantingDeviceId || revokingDeviceId) return;
    setRemoteInputGrantingDeviceId(deviceId);
    setPairingError("");
    try {
      const response = await api.createRemoteInputGrant(deviceId, REMOTE_INPUT_GRANT_TTL_SECONDS);
      if (response.ok) {
        await refreshPairedDevices();
      } else {
        setPairingError(response.error?.message ?? "无法授权这台手机远程点击。");
      }
    } catch (error) {
      setPairingError(readableError(error, "无法授权这台手机远程点击。"));
    } finally {
      setRemoteInputGrantingDeviceId("");
    }
  };

  const revokeRemoteInputGrant = async (device: MobileDevice, grant: RemoteInputGrant) => {
    const deviceId = device.device_id;
    if (!deviceId || !grant.id || remoteInputRevokingGrantId || revokingDeviceId) return;
    setRemoteInputRevokingGrantId(grant.id);
    setPairingError("");
    try {
      const response = await api.revokeRemoteInputGrant(deviceId, grant.id);
      if (response.ok) {
        await refreshPairedDevices();
      } else {
        setPairingError(response.error?.message ?? "无法撤销这次远程点击授权。");
      }
    } catch (error) {
      setPairingError(readableError(error, "无法撤销这次远程点击授权。"));
    } finally {
      setRemoteInputRevokingGrantId("");
    }
  };

  const refreshPermissionPolicy = useCallback(async () => {
    const response = await api.request<BackendPermissionPolicy>({ endpoint: "/api/settings/permission-policy" });
    if (response.ok && response.data) {
      setPermissionPolicy(mapPermissionPolicy(response.data));
      setPermissionStatus("");
    } else {
      setPermissionStatus(response.error?.message ?? "无法加载权限策略");
    }
  }, [api]);

  useEffect(() => {
    void refreshPairedDevices();
  }, [refreshPairedDevices]);

  useEffect(() => {
    void refreshPermissionPolicy();
  }, [refreshPermissionPolicy]);

  const savePermissionRule = async () => {
    setIsPermissionSaving(true);
    setPermissionStatus("");
    try {
      const rule = buildPermissionRule(permissionDraft);
      const confirmation = await api.confirmPermissionRuleChange(rule);
      const confirmationNonce = confirmation.ok && confirmation.data?.required && confirmation.data.nonce
        ? confirmation.data.nonce
        : undefined;
      const response = await api.upsertPermissionRule(rule, confirmationNonce);
      if (response.ok && response.data) {
        setPermissionPolicy(mapPermissionPolicy(response.data));
        setPermissionStatus("权限规则已保存。");
      } else {
        setPermissionStatus(response.error?.message ?? "无法保存权限规则");
      }
    } catch (error) {
      setPermissionStatus(readableError(error, "无法保存权限规则"));
    } finally {
      setIsPermissionSaving(false);
    }
  };

  const deletePermissionRule = async (ruleId: string) => {
    setPermissionStatus("");
    const confirmation = await api.confirmPermissionRuleDelete(ruleId);
    const confirmationNonce = confirmation.ok && confirmation.data?.required && confirmation.data.nonce
      ? confirmation.data.nonce
      : undefined;
    const response = await api.deletePermissionRule(ruleId, confirmationNonce);
    if (response.ok && response.data) {
      setPermissionPolicy(mapPermissionPolicy(response.data.policy));
      setPermissionStatus("权限规则已删除。");
    } else {
      setPermissionStatus(response.error?.message ?? "无法删除权限规则");
    }
  };

  const aiStatus = llmHealth
    ? llmHealth.active.available
      ? llmHealth.active.degraded
        ? "兜底模式已启用"
        : "就绪"
      : "需要配置"
    : "检查中";
  const localAiStatus = draft.mode === "efficiency"
    ? "关闭"
    : isCheckingLocalLlm
      ? "检查中"
      : detectedLocalLlmHealth
        ? detectedLocalLlmHealth.available
        ? "就绪"
        : "需要配置"
      : "检查中";
  const effectiveLocalLlmHealth = draft.mode === "efficiency" ? null : detectedLocalLlmHealth;
  const enablePrivacyMode = async () => {
    const previousDraft = draft;
    const nextDraft = {
      ...draft,
      mode: "privacy" as const,
      allowCloudContext: false,
      allowFileContentUpload: false
    };
    setDraft(nextDraft);
    setIsSaving(true);
    setSaveError("");
    setPrivacyModeStatus("");
    try {
      await onSave(nextDraft);
      const refreshedHealth = await refreshLocalLlmHealth();
      if (!refreshedHealth?.available) {
        setPrivacyModeStatus("隐私模式已开启，本地 AI 尚未就绪；后续隐私任务会停在本地修复步骤，不会静默回退云端。");
      }
    } catch (error) {
      setDraft(previousDraft);
      setPrivacyModeStatus("");
      setSaveError(readableError(error, "无法切换到隐私模式"));
    } finally {
      setIsSaving(false);
    }
  };
  const changeMode = (value: AppSettings["mode"]) => {
    if (value === "privacy") {
      void enablePrivacyMode();
      return;
    }
    setSaveError("");
    setPrivacyModeStatus("");
    setDraft((current) => ({ ...current, mode: value }));
  };
  const hardwareRuntime = providerToRuntime(draft.onnxExecutionProvider);
  const realtimeStatusText = realtimeStatus ? zhRealtimeConnectionStatus(realtimeStatus) : "";
  const realtimeStatusNeedsAttention = Boolean(realtimeStatus && realtimeStatus.state !== "open");
  const realtimeStatusProblem = Boolean(
    realtimeStatus && ["unauthorized", "policy_violation", "error", "closed", "bad_message"].includes(realtimeStatus.state)
  );
  return (
    <Panel
      title="设置"
      eyebrow="偏好"
      action={
        <Badge tone={backendStatus.state === "running" && !realtimeStatusNeedsAttention ? "success" : "warning"}>
          {realtimeStatusNeedsAttention && realtimeStatus ? zhRealtimeShortStatus(realtimeStatus) : appStatusLabel(backendStatus.state)}
        </Badge>
      }
    >
      <div className="settings-grid">
        <BasicSettingsSection
          draft={draft}
          setDraft={setDraft}
          isSaving={isSaving}
          effectiveLocalLlmHealth={effectiveLocalLlmHealth}
          detectedLocalLlmHealth={detectedLocalLlmHealth}
          localModelSetupPlan={localModelSetupPlan}
          hardwareStatus={hardwareStatus}
          privacyModeStatus={privacyModeStatus}
          aiStatus={aiStatus}
          localAiStatus={localAiStatus}
          realtimeStatusText={realtimeStatusText}
          realtimeStatusProblem={realtimeStatusProblem}
          onModeChange={changeMode}
        />
        <Suspense fallback={<div className="commerce-settings commerce-settings--loading settings-grid__full">正在读取套餐与授权...</div>}>
          <CommercePanel api={api} />
        </Suspense>
        <Suspense fallback={<div className="privacy-data-settings privacy-data-settings--loading settings-grid__full">正在准备隐私控制...</div>}>
          <PrivacyDataPanel api={api} />
        </Suspense>
        <PrivacyModeEntry
          api={api}
          draft={draft}
          privacyIntentId={privacyIntentId}
          privacyEntryRef={privacyEntryRef}
          effectiveLocalLlmHealth={effectiveLocalLlmHealth}
          detectedLocalLlmHealth={detectedLocalLlmHealth}
          localModelSetupPlan={localModelSetupPlan}
          isCheckingLocalLlm={isCheckingLocalLlm}
          isSaving={isSaving}
          onEnablePrivacy={() => void enablePrivacyMode()}
          onRefreshLocalLlmHealth={refreshLocalLlmHealth}
        />

        <details className="mcp-servers settings-grid__full">
          <summary>高级设置</summary>
          <AiConnectionSection
            draft={draft}
            setDraft={setDraft}
            llmHealth={llmHealth}
            llmCostSummary={llmCostSummary}
            backendStatus={backendStatus}
            realtimeStatusText={realtimeStatusText}
            realtimeStatusProblem={realtimeStatusProblem}
          />

          <GenerationStabilitySection
            draft={draft}
            setDraft={setDraft}
            llmHealth={llmHealth}
          />

          <DesktopInternalSettingsSection
            api={api}
            draft={draft}
            setDraft={setDraft}
            hardwareStatus={hardwareStatus}
            isCheckingHardware={isCheckingHardware}
            hardwareStatusError={hardwareStatusError}
            hardwareSmokeStatus={hardwareSmokeStatus}
            hardwareSmoke={hardwareSmoke}
            hardwareRuntime={hardwareRuntime}
            onHardwareSmokeStatusChange={setHardwareSmokeStatus}
            onHardwareSmokeChange={setHardwareSmoke}
          />

          <McpServersSection draft={draft} setDraft={setDraft} />

          <PermissionPolicyEditor
            policy={permissionPolicy}
            draft={permissionDraft}
            status={permissionStatus}
            isSaving={isPermissionSaving}
            onDraftChange={setPermissionDraft}
            onSave={() => void savePermissionRule()}
            onDelete={(ruleId) => void deletePermissionRule(ruleId)}
          />

          <RuntimeControlSection onStartBackend={onStartBackend} onStopBackend={onStopBackend} />

          <MobilePairingSection
            pairing={pairing}
            pairingCopyStatus={pairingCopyStatus}
            pairingError={pairingError}
            pairedDevices={pairedDevices}
            remoteDesktopEnabled={draft.remoteDesktopEnabled}
            isPairing={isPairing}
            revokingDeviceId={revokingDeviceId}
            remoteInputGrantingDeviceId={remoteInputGrantingDeviceId}
            remoteInputRevokingGrantId={remoteInputRevokingGrantId}
            onCopyPairingPayload={() => void copyPairingPayload()}
            onCreatePairingCode={() => void createPairingCode()}
            onCreateRemoteInputGrant={(device) => void createRemoteInputGrant(device)}
            onRevokeRemoteInputGrant={(device, grant) => void revokeRemoteInputGrant(device, grant)}
            onRevokePairedDevice={(device) => void revokePairedDevice(device)}
          />
        </details>

        <div className="button-row settings-grid__full">
          <button className="button button--primary" onClick={() => void save()} disabled={isSaving}>
            <Save size={16} aria-hidden="true" />
            {isSaving ? "保存中" : "保存设置"}
          </button>
          {saveError ? <span className="field-error" role="alert">{saveError}</span> : null}
        </div>
      </div>
    </Panel>
  );
}
