import { AlertCircle, CheckCircle2, Copy, Download, KeyRound, Loader2, MousePointer2, Play, Plus, Save, ShieldCheck, Square, Trash2, XCircle } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

import type {
  AppSettings,
  BackendStatus,
  LLMCostSummary,
  LLMHealthStatus,
  HardwareAccelerationStatusPayload,
  HardwareAccelerationSmokePayload,
  LocalLLMHealth,
  LocalModelReadiness,
  LocalModelSetupPlan,
  McpServerConfig
} from "../../shared/types";
import { buildMobilePairingQrContent, formatMobilePairingBaseUrl } from "../../shared/mobilePairingPayload";
import type { MobilePairingQrContent } from "../../shared/mobilePairingPayload";
import {
  buildRendererLoopbackBackendWebSocketUrl,
  type LengrvisApiClient,
  type MobileDevice,
  type MobilePairingCode,
  type RealtimeConnectionStatus,
  type RemoteInputGrant
} from "../lib/apiClient";
import { motionAwareScrollBehavior } from "../lib/motion";
import { activeRemoteInputGrantForDevice, mobileDeviceCanReceiveRemoteInputGrant } from "../lib/remoteInputGrant";
import { zhBackendState, zhRealtimeConnectionStatus, zhRealtimeShortStatus } from "../lib/zh";
import { Badge, Panel } from "./Panel";
import {
  buildPermissionRule,
  DEFAULT_PERMISSION_POLICY,
  DEFAULT_PERMISSION_RULE_DRAFT,
  mapPermissionPolicy,
  PermissionPolicyEditor,
  type BackendPermissionPolicy,
  type PermissionPolicy
} from "./settings/PermissionPolicyEditor";
import {
  LocalLlmHealthNotice,
  localModelUserMessage,
  ModelBoundaryProfile,
  modelDisplayName
} from "./settings/LocalModelSettings";

function zhMode(mode: AppSettings["mode"]): string {
  return displayMode(mode);
}

function displayMode(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "快速";
  if (mode === "hybrid") return "智能混合";
  return "隐私";
}

const PERMISSION_MODE_OPTIONS: Array<{
  value: AppSettings["permissionMode"];
  label: string;
  description: string;
}> = [
  { value: "plan", label: "计划", description: "只允许规划和读取。" },
  { value: "default", label: "默认", description: "写操作需要试运行审批。" },
  { value: "trusted_edits", label: "可信编辑", description: "放行可逆可信编辑。" },
  { value: "auto_review", label: "自动审查", description: "规则和安全审查共同放行。" },
  { value: "dont_ask", label: "不打扰", description: "只执行预授权动作。" },
];

function permissionModeLabel(mode: AppSettings["permissionMode"]): string {
  return PERMISSION_MODE_OPTIONS.find((option) => option.value === mode)?.label ?? "默认";
}

function modeDescription(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "云端优先，适合长推理和网页任务。";
  if (mode === "hybrid") return "云端规划，本机处理敏感内容。";
  return "本机优先，失败时给修复动作。";
}

function appStatusLabel(state: BackendStatus["state"]): string {
  if (state === "running") return "就绪";
  if (state === "starting") return "启动中";
  if (state === "error") return "需要处理";
  return "不可用";
}

const LOCAL_MODEL_OPTIONS = [
  { value: "qwen2.5:3b", label: "Qwen2.5 3B" },
  { value: "qwen2.5:7b", label: "Qwen2.5 7B" },
  { value: "llama3.2:3b", label: "Llama 3.2 3B" }
] as const;

const INSTALL_MODEL_WS_PATHS = ["/ws/settings/install-local-model", "/api/ws/settings/install-local-model"] as const;
const INSTALL_MODEL_WS_RETRY_DELAY_MS = 2_500;
const INSTALL_MODEL_WS_MAX_RETRIES = 4;
const REMOTE_INPUT_GRANT_TTL_SECONDS = 5 * 60;
type HardwareRuntime = "auto" | "winml" | "directml" | "openvino" | "cpu";

interface InstallModelProgress {
  stage: string;
  percent: number;
  error?: string;
}

type InstallModelStatus = "idle" | "installing" | "completed" | "error";
type InstallModelSocketStatus = "idle" | "connecting" | "connected" | "reconnecting" | "closed";

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

const PairingVisualCode = lazy(() =>
  import("./settings/PairingVisualCode").then((module) => ({ default: module.PairingVisualCode }))
);
const HardwareAccelerationCard = lazy(() =>
  import("./settings/HardwareAccelerationCard").then((module) => ({ default: module.HardwareAccelerationCard }))
);
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
  const pairingQrContent = pairing ? buildMobilePairingQrContent(pairing) : null;
  const pairingBaseUrl = pairing ? formatMobilePairingBaseUrl(pairing) : "";
  const pairingTransportWarning = pairingBaseUrl ? mobilePairingTransportWarning(pairingBaseUrl) : "";
  const pairingTransportSummary = pairing && pairingBaseUrl ? mobilePairingTransportSummary(pairing, pairingBaseUrl) : null;
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
                      onChange={() => changeMode(value)}
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
        <Suspense fallback={<div className="commerce-settings commerce-settings--loading settings-grid__full">正在读取套餐与授权...</div>}>
          <CommercePanel api={api} />
        </Suspense>
        <Suspense fallback={<div className="privacy-data-settings privacy-data-settings--loading settings-grid__full">正在准备隐私控制...</div>}>
          <PrivacyDataPanel api={api} />
        </Suspense>
        {draft.mode === "privacy" || draft.mode === "hybrid" ? (
          <div
            ref={privacyEntryRef}
            className={privacyIntentId === undefined ? "settings-grid__full settings-privacy-anchor" : "settings-grid__full settings-privacy-anchor settings-privacy-anchor--intent"}
            tabIndex={-1}
          >
            {privacyIntentId !== undefined ? <PrivacyFlowHint /> : null}
            <LocalModelInstaller
              api={api}
              apiBaseUrl={draft.apiBaseUrl}
              readiness={effectiveLocalLlmHealth?.readiness}
              health={effectiveLocalLlmHealth}
              setupPlan={localModelSetupPlan}
              mode={draft.mode}
              onHealthRefresh={refreshLocalLlmHealth}
            />
          </div>
        ) : (
          <div
            ref={privacyEntryRef}
            className={privacyIntentId === undefined ? "settings-grid__full settings-privacy-anchor" : "settings-grid__full settings-privacy-anchor settings-privacy-anchor--intent"}
            tabIndex={-1}
          >
            {privacyIntentId !== undefined ? <PrivacyFlowHint /> : null}
            <PrivacyReadinessPanel
              mode={draft.mode}
              health={detectedLocalLlmHealth}
              setupPlan={localModelSetupPlan}
              checking={isCheckingLocalLlm}
              onEnablePrivacy={() => void enablePrivacyMode()}
              onRefresh={() => void refreshLocalLlmHealth()}
              disabled={isSaving}
            />
          </div>
        )}

        <details className="mcp-servers settings-grid__full">
          <summary>高级设置</summary>
          <fieldset className="mcp-servers">
            <legend>AI 连接</legend>
            <div className="settings-grid settings-grid--balanced">
              <label className="field">
                <span>服务商</span>
                <input value={draft.providerName} onChange={(event) => setDraft((current) => ({ ...current, providerName: event.target.value }))} />
              </label>
              <label className="field">
                <span>模型</span>
                <input list="lengrvis-model-options" value={draft.model} onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))} placeholder="选择或输入模型名" />
              </label>
              <label className="field">
                <span>审核模型</span>
                <input list="lengrvis-model-options" value={draft.reviewModel} onChange={(event) => setDraft((current) => ({ ...current, reviewModel: event.target.value }))} placeholder="选择或输入模型名" />
              </label>
              <label className="field">
                <span>接口类型</span>
                <select value={draft.wireApi} onChange={(event) => setDraft((current) => ({ ...current, wireApi: event.target.value as AppSettings["wireApi"] }))}>
                  <option value="chat_completions">对话补全接口（chat_completions）</option>
                  <option value="responses">响应式接口（responses）</option>
                </select>
              </label>
              <label className="field">
                <span>推理强度</span>
                <select value={draft.modelReasoningEffort} onChange={(event) => setDraft((current) => ({ ...current, modelReasoningEffort: event.target.value }))}>
                  <option value="">默认（跟随模型）</option>
                  <option value="minimal">最小</option>
                  <option value="low">低</option>
                  <option value="medium">中</option>
                  <option value="high">高</option>
                  {["", "minimal", "low", "medium", "high"].includes(draft.modelReasoningEffort) ? null : (
                    <option value={draft.modelReasoningEffort}>{draft.modelReasoningEffort}（自定义）</option>
                  )}
                </select>
              </label>
              <datalist id="lengrvis-model-options">
                <option value="gpt-4o-mini" />
                <option value="gpt-4o" />
                <option value="gpt-4.1-mini" />
                <option value="gpt-4.1" />
                <option value="o4-mini" />
                <option value="qwen2.5:7b-instruct" />
                <option value="qwen2.5:3b-instruct" />
                <option value="llama3.1:8b" />
              </datalist>
              <label className="field">
                <span>服务商 Base URL</span>
                <input value={draft.apiBaseUrl} onChange={(event) => setDraft((current) => ({ ...current, apiBaseUrl: event.target.value }))} />
              </label>
              <label className="mcp-servers__toggle">
                <input
                  type="checkbox"
                  checked={draft.requiresOpenAiAuth}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, requiresOpenAiAuth: event.target.checked }))
                  }
                />
                <span>需要 OpenAI 认证</span>
              </label>
              <label className="mcp-servers__toggle">
                <input
                  type="checkbox"
                  checked={draft.disableResponseStorage}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, disableResponseStorage: event.target.checked }))
                  }
                />
                <span>禁用响应存储</span>
              </label>
            </div>
            <div className="settings-status-grid">
              <p className="muted">当前：{llmHealth?.active.provider ?? "N/A"} / {llmHealth?.active.model ?? "N/A"} / {llmHealth?.active.profile.activeBackend ?? "N/A"}</p>
              <p className="muted">成本：{llmCostSummary ? `${llmCostSummary.calls} 次调用，${llmCostSummary.totalTokens} tokens，${llmCostSummary.totalCostUsd === null ? "N/A" : `$${llmCostSummary.totalCostUsd.toFixed(4)}`}` : "N/A"}</p>
              <p className="muted">运行状态：{zhBackendState(backendStatus.state)}</p>
              {realtimeStatusText ? (
                <p className={realtimeStatusProblem ? "settings-status settings-status--error" : "muted"}>
                  实时状态：{realtimeStatusText}
                </p>
              ) : null}
            </div>
          </fieldset>

          <fieldset className="mcp-servers">
            <legend>生成与稳定性</legend>
            <div className="settings-grid settings-grid--balanced">
              <label className="field">
                <span>温度</span>
                <input type="number" min={0} max={2} step={0.05} value={draft.temperature} onChange={(event) => setDraft((current) => ({ ...current, temperature: Number(event.target.value) || 0 }))} />
              </label>
              <label className="field">
                <span>最大 Tokens</span>
                <input type="number" min={1} step={1} value={draft.maxTokens} onChange={(event) => setDraft((current) => ({ ...current, maxTokens: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
              <label className="field">
                <span>超时</span>
                <input type="number" min={1} step={1} value={draft.timeout} onChange={(event) => setDraft((current) => ({ ...current, timeout: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
              <label className="field">
                <span>重试次数</span>
                <input type="number" min={0} step={1} value={draft.llmApiMaxRetries} onChange={(event) => setDraft((current) => ({ ...current, llmApiMaxRetries: Math.max(0, Number(event.target.value) || 0) }))} />
              </label>
              <label className="field">
                <span>重试退避</span>
                <input type="number" min={0} step={0.05} value={draft.llmApiRetryBackoffSeconds} onChange={(event) => setDraft((current) => ({ ...current, llmApiRetryBackoffSeconds: Math.max(0, Number(event.target.value) || 0) }))} />
              </label>
              <label className="field">
                <span>熔断阈值</span>
                <input type="number" min={1} step={1} value={draft.llmApiCircuitFailureThreshold} onChange={(event) => setDraft((current) => ({ ...current, llmApiCircuitFailureThreshold: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
              <label className="field">
                <span>熔断冷却</span>
                <input type="number" min={0} step={1} value={draft.llmApiCircuitCooldownSeconds} onChange={(event) => setDraft((current) => ({ ...current, llmApiCircuitCooldownSeconds: Math.max(0, Number(event.target.value) || 0) }))} />
              </label>
              <label className="field">
                <span>上下文窗口</span>
                <input type="number" min={1} step={1} value={draft.modelContextWindow} onChange={(event) => setDraft((current) => ({ ...current, modelContextWindow: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
              <label className="field">
                <span>自动压缩上限</span>
                <input type="number" min={1} step={1} value={draft.modelAutoCompactTokenLimit} onChange={(event) => setDraft((current) => ({ ...current, modelAutoCompactTokenLimit: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
            </div>
            <div className="settings-status-grid">
              <p className="muted">重试：{llmHealth?.retry.maxRetries ?? "N/A"} 次，退避 {llmHealth?.retry.backoffSeconds ?? "N/A"} 秒，熔断状态 {llmHealth?.retry.circuit.state ?? "N/A"}</p>
            </div>
          </fieldset>

          <fieldset className="mcp-servers">
            <legend>桌面端内部设置</legend>
            <div className="settings-grid settings-grid--balanced">
              <label className="field">
                <span>允许的应用</span>
                <textarea
                  value={draft.appAllowlist.join("; ")}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      appAllowlist: splitSettingList(event.target.value)
                    }))
                  }
                />
              </label>
              <label className="field">
                <span>浏览器截图目录</span>
                <input
                  value={draft.browserScreenshotDir}
                  onChange={(event) => setDraft((current) => ({ ...current, browserScreenshotDir: event.target.value }))}
                />
              </label>
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
              <label className="field">
                <span>文本向量后端</span>
                <input
                  value={draft.onnxEmbeddingBackend}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxEmbeddingBackend: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>文本向量模型路径</span>
                <input
                  value={draft.onnxEmbeddingModelPath}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxEmbeddingModelPath: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>文本向量 EP</span>
                <input
                  value={draft.onnxEmbeddingExecutionProvider}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxEmbeddingExecutionProvider: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>图像向量后端</span>
                <input
                  value={draft.imageEmbeddingBackend}
                  onChange={(event) => setDraft((current) => ({ ...current, imageEmbeddingBackend: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>图像向量模型路径</span>
                <input
                  value={draft.onnxImageEmbeddingModelPath}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxImageEmbeddingModelPath: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>OCR 后端</span>
                <input
                  value={draft.ocrBackend}
                  onChange={(event) => setDraft((current) => ({ ...current, ocrBackend: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>OCR EP</span>
                <input
                  value={draft.ocrExecutionProvider}
                  onChange={(event) => setDraft((current) => ({ ...current, ocrExecutionProvider: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>网页读取上限</span>
                <input
                  type="number"
                  min={1000}
                  step={1000}
                  value={draft.browserMaxPageBytes}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      browserMaxPageBytes: Math.max(1000, Number(event.target.value) || 1000)
                    }))
                  }
                />
              </label>
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
                onSmokeStatusChange={setHardwareSmokeStatus}
                onSmokeChange={setHardwareSmoke}
              />
            </Suspense>
          </fieldset>

          <fieldset className="mcp-servers">
            <legend>工具连接</legend>
            {draft.mcpServers.length === 0 ? (
              <p className="muted">尚未配置工具连接。</p>
            ) : null}
            <ul className="mcp-servers__list">
              {draft.mcpServers.map((server, index) => (
                <li className="mcp-servers__row mcp-servers__row--server" key={index}>
                  <input
                    placeholder="名称"
                    value={server.name}
                    onChange={(event) => updateMcpServer(setDraft, index, { name: event.target.value })}
                  />
                  <input
                    placeholder="URL"
                    value={server.url}
                    onChange={(event) => updateMcpServer(setDraft, index, { url: event.target.value })}
                  />
                  <input
                    placeholder="命令"
                    value={server.command ?? ""}
                    onChange={(event) => updateMcpServer(setDraft, index, { command: event.target.value })}
                  />
                  <input
                    placeholder="参数"
                    value={server.args?.join("; ") ?? ""}
                    onChange={(event) => updateMcpServer(setDraft, index, { args: splitSettingList(event.target.value) })}
                  />
                  <input
                    placeholder="传输方式"
                    value={server.transport ?? ""}
                    onChange={(event) => updateMcpServer(setDraft, index, { transport: event.target.value })}
                  />
                  <label className="mcp-servers__toggle">
                    <input
                      type="checkbox"
                      checked={server.enabled}
                      onChange={(event) => updateMcpServer(setDraft, index, { enabled: event.target.checked })}
                    />
                    <span>启用</span>
                  </label>
                  <button
                    type="button"
                    className="button button--ghost"
                    onClick={() => removeMcpServer(setDraft, index)}
                    aria-label="删除工具连接"
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
            <button type="button" className="button button--ghost" onClick={() => addMcpServer(setDraft)}>
              <Plus size={14} aria-hidden="true" />
              添加工具连接
            </button>
          </fieldset>

          <PermissionPolicyEditor
            policy={permissionPolicy}
            draft={permissionDraft}
            status={permissionStatus}
            isSaving={isPermissionSaving}
            onDraftChange={setPermissionDraft}
            onSave={() => void savePermissionRule()}
            onDelete={(ruleId) => void deletePermissionRule(ruleId)}
          />

          <fieldset className="mcp-servers">
            <legend>运行控制</legend>
            <div className="button-row">
              <button className="button button--secondary" onClick={() => void onStartBackend()}>
                <Play size={16} aria-hidden="true" />
                启动
              </button>
              <button className="button button--secondary" onClick={() => void onStopBackend()}>
                <Square size={16} aria-hidden="true" />
                停止
              </button>
            </div>
          </fieldset>

          <div className="mobile-pairing">
            <div className="mobile-pairing__copy">
              <strong>手机扫码配对</strong>
              <span>点击生成后，打开手机 App 的扫码入口扫二维码；桌面地址、端口和一次性配对码会一起带过去。</span>
              {pairing ? (
                <div className="mobile-pairing__payload" aria-label="手机扫码配对状态">
                  <div className="mobile-pairing__payload-head">
                    <small>
                      二维码已生成：{pairingBaseUrl} · {new Date(pairing.expires_at).toLocaleTimeString()} 过期
                    </small>
                    <button
                      type="button"
                      className="button button--ghost mobile-pairing__copy-button"
                      onClick={() => void copyPairingPayload()}
                      aria-label="复制备用手机配对信息"
                      title="复制备用手机配对信息"
                    >
                      {pairingCopyStatus.startsWith("已复制") ? <CheckCircle2 size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
                      备用复制
                    </button>
                  </div>
                  <small>优先扫码；复制只是备用，不会在界面展开 token。</small>
                  {pairingCopyStatus ? (
                    <small className="mobile-pairing__copy-status" role="status">
                      {pairingCopyStatus}
                    </small>
                  ) : null}
                  {pairingTransportWarning ? (
                    <small className="mobile-pairing__error" role="status">
                      {pairingTransportWarning}
                    </small>
                  ) : null}
                  {pairingTransportSummary ? (
                    <div
                      className={`mobile-pairing__transport mobile-pairing__transport--${pairingTransportSummary.tone}`}
                      aria-label="手机连接安全状态"
                    >
                      <div className="mobile-pairing__transport-head">
                        {pairingTransportSummary.tone === "ready" ? (
                          <ShieldCheck size={14} aria-hidden="true" />
                        ) : (
                          <AlertCircle size={14} aria-hidden="true" />
                        )}
                        <strong>{pairingTransportSummary.label}</strong>
                      </div>
                      <span>{pairingTransportSummary.detail}</span>
                      <dl>
                        <div>
                          <dt>HTTPS</dt>
                          <dd>{pairingTransportSummary.origin}</dd>
                        </div>
                        <div>
                          <dt>WSS</dt>
                          <dd>{pairingTransportSummary.wssPaths.join(" · ")}</dd>
                        </div>
                        {pairingTransportSummary.fingerprint ? (
                          <div>
                            <dt>证书 SHA-256</dt>
                            <dd className="mobile-pairing__fingerprint">{pairingTransportSummary.fingerprint}</dd>
                          </div>
                        ) : null}
                      </dl>
                      {pairingTransportSummary.trustNotice ? <span>{pairingTransportSummary.trustNotice}</span> : null}
                      <span>真机证据仍需单独采集：扫码配对、审批 WSS、远程屏幕、输入授权、撤销和过期都不会由桌面 UI 自动标记通过。</span>
                    </div>
                  ) : null}
                </div>
              ) : (
                <small>先生成二维码，然后打开手机 App 扫码。无需手动输入局域网地址或 token。</small>
              )}
              <small>HTTPS/WSS 会直接用于手机连接；局域网 HTTP 会被拦截，请在电脑端启用 HTTPS/WSS 后重新生成。</small>
              {pairedDevices.length ? (
                <ul className="mobile-pairing__devices" aria-label="已配对手机">
                  {pairedDevices.map((device) => {
                    const activeGrant = activeRemoteInputGrantForDevice(device);
                    return (
                      <li key={device.device_id}>
                        <div className="mobile-pairing__device-main">
                          <span>{device.device_name || device.device_id}</span>
                          <small>{device.device_id}</small>
                          <div className="mobile-pairing__chips" aria-label="设备权限和状态">
                            {mobileDevicePermissionChips(device, draft.remoteDesktopEnabled).map((chip) => (
                              <em key={chip}>{chip}</em>
                            ))}
                          </div>
                          {activeGrant ? (
                            <small className="mobile-pairing__grant-status">
                              远程点击授权至 {formatDeviceDate(activeGrant.expires_at)}
                            </small>
                          ) : null}
                          <small>
                            {device.revoked_at
                              ? `已于 ${formatDeviceDate(device.revoked_at)} 断开`
                              : `最后同步 ${formatDeviceDate(device.updated_at)}`}
                          </small>
                        </div>
                        <div className="mobile-pairing__device-actions">
                          {activeGrant ? (
                            <button
                              type="button"
                              className="button button--ghost mobile-pairing__action mobile-pairing__action--remote"
                              onClick={() => void revokeRemoteInputGrant(device, activeGrant)}
                              disabled={remoteInputRevokingGrantId === activeGrant.id || revokingDeviceId === device.device_id}
                              aria-label={`撤销手机 ${device.device_name || device.device_id} 的远程点击授权`}
                              title="撤销远程点击授权"
                            >
                              {remoteInputRevokingGrantId === activeGrant.id ? (
                                <Loader2 className="settings-spinner" size={14} aria-hidden="true" />
                              ) : (
                                <XCircle size={14} aria-hidden="true" />
                              )}
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="button button--ghost mobile-pairing__action mobile-pairing__action--remote"
                              onClick={() => void createRemoteInputGrant(device)}
                              disabled={
                                !mobileDeviceCanReceiveRemoteInputGrant(device, draft.remoteDesktopEnabled) ||
                                remoteInputGrantingDeviceId === device.device_id ||
                                revokingDeviceId === device.device_id
                              }
                              aria-label={`授权手机 ${device.device_name || device.device_id} 远程点击`}
                              title="授权远程点击"
                            >
                              {remoteInputGrantingDeviceId === device.device_id ? (
                                <Loader2 className="settings-spinner" size={14} aria-hidden="true" />
                              ) : (
                                <MousePointer2 size={14} aria-hidden="true" />
                              )}
                            </button>
                          )}
                          <button
                            type="button"
                            className="button button--ghost mobile-pairing__action mobile-pairing__action--revoke"
                            onClick={() => void revokePairedDevice(device)}
                            disabled={revokingDeviceId === device.device_id}
                            aria-label={`断开手机 ${device.device_name || device.device_id}`}
                            title="断开手机"
                          >
                            {revokingDeviceId === device.device_id ? (
                              <Loader2 className="settings-spinner" size={14} aria-hidden="true" />
                            ) : (
                              <Trash2 size={14} aria-hidden="true" />
                            )}
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <small>暂无已配对设备。</small>
              )}
              <small>
                指针按钮会给该手机 5 分钟远程点击授权；每次点击仍会在电脑端生成审批。断开设备会立即撤销它的审批、屏幕查看和远程输入令牌；重新使用需要重新配对。
              </small>
              {pairingError ? <small className="mobile-pairing__error">{pairingError}</small> : null}
            </div>
            <Suspense fallback={<PairingVisualCodeFallback code={pairing?.code} />}>
              <PairingVisualCode code={pairing?.code} qrContent={pairingQrContent} />
            </Suspense>
            <button className="button button--secondary" onClick={() => void createPairingCode()} disabled={isPairing} type="button">
              {isPairing ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />}
              生成配对码
            </button>
          </div>
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

function LocalModelInstaller({
  api,
  apiBaseUrl,
  readiness,
  health,
  setupPlan,
  mode,
  onHealthRefresh
}: {
  api: LengrvisApiClient;
  apiBaseUrl: string;
  readiness?: LocalModelReadiness;
  health: LocalLLMHealth | null;
  setupPlan: LocalModelSetupPlan | null;
  mode: AppSettings["mode"];
  onHealthRefresh?: () => Promise<LocalLLMHealth | null>;
}) {
  const initialModel = localModelOptionValue(readiness?.recommendedModel);
  const [model, setModel] = useState<(typeof LOCAL_MODEL_OPTIONS)[number]["value"]>(initialModel);
  const [status, setStatus] = useState<InstallModelStatus>("idle");
  const [socketStatus, setSocketStatus] = useState<InstallModelSocketStatus>("idle");
  const [progress, setProgress] = useState<InstallModelProgress>({
    stage: "选择模型后即可准备到这台电脑。",
    percent: 0
  });
  const [selectedSetupPlan, setSelectedSetupPlan] = useState<LocalModelSetupPlan | null>(setupPlan);
  const [isCheckingSetupPlan, setIsCheckingSetupPlan] = useState(false);
  const closeProgressSocketRef = useRef<() => void>();

  const isInstalling = status === "installing";
  const effectiveSetupPlan = selectedSetupPlan ?? setupPlan;
  const effectiveReadiness = effectiveSetupPlan?.readiness ?? readiness;
  const canInstall = effectiveSetupPlan?.canInstall ?? readiness?.canInstall ?? true;
  const lastError = status === "error" ? localModelUserMessage(progress.error || progress.stage, "安装失败，请重新检查或重试。") : "";

  useEffect(() => {
    const recommendedModel = effectiveReadiness?.recommendedModel;
    if (recommendedModel && status === "idle") {
      setModel(localModelOptionValue(recommendedModel));
    }
  }, [effectiveReadiness?.recommendedModel, status]);

  useEffect(() => {
    if (setupPlan && localModelOptionValue(setupPlan.model) === model) {
      setSelectedSetupPlan(setupPlan);
    }
  }, [model, setupPlan]);

  const closeProgressSocket = useCallback(() => {
    closeProgressSocketRef.current?.();
    closeProgressSocketRef.current = undefined;
  }, []);

  useEffect(() => closeProgressSocket, [closeProgressSocket]);

  const refreshSelectedSetupPlan = useCallback(async () => {
    setIsCheckingSetupPlan(true);
    try {
      const response = await api.getLocalModelSetupPlan(model);
      if (response.ok && response.data) {
        setSelectedSetupPlan(response.data);
        return response.data;
      }
    } finally {
      setIsCheckingSetupPlan(false);
    }
    return null;
  }, [api, model]);

  useEffect(() => {
    void refreshSelectedSetupPlan();
  }, [refreshSelectedSetupPlan]);

  const applyProgress = useCallback(
    (nextProgress: InstallModelProgress) => {
      const normalizedProgress = normalizeInstallModelProgress(nextProgress);
      setProgress(normalizedProgress);

      if (normalizedProgress.error) {
        setStatus("error");
        closeProgressSocket();
        return;
      }

      if (normalizedProgress.percent >= 100) {
        setStatus("completed");
        setSocketStatus("closed");
        closeProgressSocket();
        void onHealthRefresh?.();
        void refreshSelectedSetupPlan();
      }
    },
    [closeProgressSocket, onHealthRefresh, refreshSelectedSetupPlan]
  );

  const startInstallRequest = useCallback(
    async (fallbackStage?: string) => {
      closeProgressSocket();
      if (fallbackStage) {
        setProgress({ stage: fallbackStage, percent: 1 });
      }

      const response = await api.installLocalModel({ model });

      if (!response.ok) {
        setStatus("error");
        setProgress({
          stage: localModelUserMessage(response.error?.message, "安装请求失败，请确认 Lengrvis 正在运行。"),
          percent: 0,
          error: localModelUserMessage(response.error?.message, "安装请求失败")
        });
        return;
      }

      const responseProgress = latestInstallModelProgress(response.data);
      if (responseProgress) {
        applyProgress(responseProgress);
      }
      const responsePercent = responseProgress ? clampPercent(responseProgress.percent) : 0;

      if (response.data?.ok === false || response.data?.error) {
        setStatus("error");
        setProgress({
          stage: localModelUserMessage(response.data.error ?? response.data.message, "安装任务启动失败。"),
          percent: responseProgress ? responsePercent : 0,
          error: localModelUserMessage(response.data.error ?? response.data.message, "安装任务启动失败")
        });
        return;
      }

      if (responseProgress && responsePercent >= 100) {
        setSocketStatus("closed");
        setStatus("completed");
        void onHealthRefresh?.();
        void refreshSelectedSetupPlan();
        return;
      }

      setProgress((current) =>
        current.percent > 0
          ? current
          : {
              stage: localModelUserMessage(response.data?.message, "安装任务已启动，正在等待进度..."),
              percent: 1
            }
      );
    },
    [api, applyProgress, closeProgressSocket, model, onHealthRefresh, refreshSelectedSetupPlan]
  );

  const openProgressSocket = useCallback((): boolean => {
    closeProgressSocket();

    let unsubscribeSocket: (() => void) | null = null;
    let closedByCaller = false;
    let retryId: number | undefined;
    let pathIndex = 0;
    let receivedProgress = false;
    let reconnectAttempts = 0;
    let fallbackStarted = false;

    const connect = (): boolean => {
      setSocketStatus(pathIndex === 0 && !receivedProgress ? "connecting" : "reconnecting");
      const nextUnsubscribe = subscribeInstallModelProgressSocket(
        apiBaseUrl,
        INSTALL_MODEL_WS_PATHS[pathIndex],
        model,
        {
          onOpen: () => {
            setSocketStatus("connected");
          },
          onMessage: (data) => {
            receivedProgress = true;
            const nextProgress = parseInstallModelProgress(data);
            if (nextProgress) {
              applyProgress(nextProgress);
            }
          },
          onError: () => {
            setSocketStatus("reconnecting");
          },
          onClose: () => {
            unsubscribeSocket = null;
            handleSocketClose();
          }
        }
      );

      if (!nextUnsubscribe) {
        setSocketStatus("closed");
        return false;
      }

      unsubscribeSocket = nextUnsubscribe;
      return true;
    };

    const handleSocketClose = () => {
        if (closedByCaller) {
          setSocketStatus("closed");
          return;
        }
        if (!receivedProgress && pathIndex < INSTALL_MODEL_WS_PATHS.length - 1) {
          pathIndex += 1;
          reconnectAttempts = 0;
        } else {
          reconnectAttempts += 1;
        }
        if (reconnectAttempts >= INSTALL_MODEL_WS_MAX_RETRIES) {
          setSocketStatus("closed");
          if (!receivedProgress && !fallbackStarted) {
            fallbackStarted = true;
            void startInstallRequest("进度连接不可用，已切换为普通安装请求；仍会继续完成本地模型准备。");
          } else {
            setStatus("error");
            setProgress({
              stage: "安装进度连接中断，请重新检查或重试。",
              percent: 0,
              error: "安装进度连接中断，请重新检查或重试。"
            });
            void onHealthRefresh?.();
          }
          return;
        }
        retryId = window.setTimeout(() => {
          void connect();
        }, INSTALL_MODEL_WS_RETRY_DELAY_MS);
    };

    if (!connect()) {
      return false;
    }

    closeProgressSocketRef.current = () => {
      closedByCaller = true;
      if (retryId !== undefined) window.clearTimeout(retryId);
      unsubscribeSocket?.();
      unsubscribeSocket = null;
      setSocketStatus("closed");
    };

    return true;
  }, [apiBaseUrl, applyProgress, closeProgressSocket, model, onHealthRefresh, startInstallRequest]);

  const installModel = async () => {
    if (!canInstall) {
      setStatus("error");
      setProgress({
        stage: effectiveReadiness?.reason ?? "这台电脑暂不满足本地 AI 推荐条件。",
        percent: 0,
        error: effectiveReadiness?.reason ?? "这台电脑暂不满足本地 AI 推荐条件。"
      });
      return;
    }
    setStatus("installing");
    setProgress({ stage: installModelStartStage(effectiveSetupPlan, model), percent: 0 });
    const usingSocket = openProgressSocket();
    if (usingSocket) {
      return;
    }

    await startInstallRequest();
  };

  const tone =
    status === "completed"
      ? "success"
      : status === "error"
        ? "danger"
        : isInstalling
          ? "info"
          : "neutral";

  return (
    <div className="local-model-installer">
      <PrivacyReadinessPanel
        mode={mode}
        health={health}
        setupPlan={effectiveSetupPlan}
        checking={isInstalling || isCheckingSetupPlan}
        error={lastError}
        onPrimaryAction={() => void installModel()}
        onRefresh={() => void onHealthRefresh?.()}
        disabled={isInstalling || !canInstall}
      />
      <div className="local-model-installer__head">
        <div className="local-model-installer__copy">
          <strong>本地 AI 备选设置</strong>
          <span>
            {localModelInstallerHint(effectiveSetupPlan, model)}
          </span>
        </div>
        <Badge tone={tone}>{zhInstallModelStatus(status, socketStatus)}</Badge>
      </div>

      {effectiveReadiness ? <LocalModelReadinessView readiness={effectiveReadiness} /> : null}

      <div className="local-model-installer__controls">
        <label className="field">
          <span>模型</span>
          <select
            value={model}
            disabled={isInstalling}
            onChange={(event) => setModel(event.target.value as (typeof LOCAL_MODEL_OPTIONS)[number]["value"])}
          >
            {LOCAL_MODEL_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label} ({option.value})
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button button--secondary local-model-installer__button"
          disabled={isInstalling || !canInstall}
          onClick={() => void installModel()}
        >
          {isInstalling ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
          {isInstalling ? "正在安装" : "安装所选模型"}
        </button>
      </div>

      <InstallModelProgressBar progress={progress} />
      {progress.error ? (
        <span className="settings-status settings-status--error" role="alert">{localModelUserMessage(progress.error, "安装失败，请重新检查或重试。")}</span>
      ) : null}
    </div>
  );
}

function InstallModelProgressBar({ progress }: { progress: InstallModelProgress }) {
  const percent = clampPercent(progress.percent);

  return (
    <div className="local-model-progress">
      <div className="local-model-progress__meta">
        <span className="local-model-progress__stage">
          {progress.stage}
        </span>
        <span className="local-model-progress__percent">
          {percent}%
        </span>
      </div>
      <progress
        className={progress.error ? "local-model-progress__track local-model-progress__track--error" : "local-model-progress__track"}
        aria-label="本地模型安装进度"
        value={percent}
        max={100}
      />
    </div>
  );
}

function PrivacyReadinessPanel({
  mode,
  health,
  setupPlan,
  checking,
  disabled = false,
  error = "",
  onEnablePrivacy,
  onPrimaryAction,
  onRefresh
}: {
  mode: AppSettings["mode"];
  health: LocalLLMHealth | null;
  setupPlan: LocalModelSetupPlan | null;
  checking: boolean;
  disabled?: boolean;
  error?: string;
  onEnablePrivacy?: () => void;
  onPrimaryAction?: () => void;
  onRefresh?: () => void;
}) {
  const steps = buildPrivacyReadinessSteps(mode, health, setupPlan);
  const ready = mode !== "efficiency" && (Boolean(health?.available) || Boolean(setupPlan?.ready));
  const blocked = steps.some((step) => step.state === "blocked");
  const tone = error || blocked ? "danger" : ready ? "success" : checking ? "info" : "warning";
  const primaryAction = privacyReadinessPrimaryAction(mode, setupPlan, ready, blocked, checking);
  const PrimaryIcon = privacyReadinessPrimaryIcon(primaryAction?.kind, checking);
  const repairNote = privacyReadinessRepairNote(mode, health, setupPlan, ready, checking);

  return (
    <section className={`privacy-readiness privacy-readiness--${tone}`} aria-label="隐私模式开箱检查">
      <div className="privacy-readiness__head">
        <div>
          <strong>{ready ? "隐私模式已就绪" : "隐私模式开箱检查"}</strong>
          <span>{privacyReadinessSummary(mode, health, setupPlan, checking)}</span>
        </div>
        <Badge tone={tone}>{error ? "需要处理" : ready ? "可本地处理" : blocked ? "条件不足" : checking ? "检查中" : "待配置"}</Badge>
      </div>
      {error ? (
        <div className="privacy-readiness__alert" role="alert">
          <AlertCircle size={16} aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : null}
      <ol className="privacy-readiness__steps">
        {steps.map((step) => (
          <li key={step.key} className={`privacy-step privacy-step--${step.state}`}>
            <span className="privacy-step__dot" aria-hidden="true" />
            <div>
              <strong>{step.label}</strong>
              <small>{step.detail}</small>
            </div>
          </li>
        ))}
      </ol>
      <div className="privacy-readiness__actions">
        {mode === "efficiency" && onEnablePrivacy ? (
          <button className="button button--primary" type="button" onClick={onEnablePrivacy} disabled={disabled}>
            <ShieldCheck size={16} aria-hidden="true" />
            开启隐私模式
          </button>
        ) : null}
        {mode !== "efficiency" && primaryAction && onPrimaryAction ? (
          <button
            className="button button--primary"
            type="button"
            onClick={onPrimaryAction}
            disabled={disabled || primaryAction.disabled}
          >
            <PrimaryIcon className={checking ? "settings-spinner" : undefined} size={16} aria-hidden="true" />
            {primaryAction.label}
          </button>
        ) : null}
        <button className="button button--secondary" type="button" onClick={onRefresh} disabled={disabled || checking}>
          {checking ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <CheckCircle2 size={16} aria-hidden="true" />}
          重新检查
        </button>
      </div>
      {setupPlan ? (
        <PrivacyBundleStatus setupPlan={setupPlan} />
      ) : null}
      {setupPlan ? (
        <LocalModelEvidenceSummary setupPlan={setupPlan} />
      ) : null}
      {repairNote ? <p className="privacy-readiness__note">{repairNote}</p> : null}
      <p className="privacy-readiness__note">
        {mode === "efficiency"
          ? "开启隐私模式只会关闭云端辅助并检查本地 AI；下一步再按提示准备本地模型，不会静默回退云端。"
          : "主程序安装包不内置大模型；主按钮会按顺序安装本地 AI 应用、启动本地服务，并联网下载或启用推荐模型。下载完成后可用于断网隐私任务，失败会停在本地修复步骤，不会静默回退云端。"}
      </p>
    </section>
  );
}

function PrivacyFlowHint() {
  return (
    <div className="privacy-flow-hint" role="status">
      <div>
        <strong>从首页开箱检查来到这里</strong>
        <span>先开启隐私模式，再按下一步准备本地 AI；没有本地 AI 时，隐私任务不会静默退回云端。</span>
      </div>
      <span>下一步看这里</span>
    </div>
  );
}

type PrivacyReadinessStepState = "pending" | "done" | "current" | "blocked";
type PrivacyReadinessStep = { key: string; label: string; detail: string; state: PrivacyReadinessStepState };

function buildPrivacyReadinessSteps(
  mode: AppSettings["mode"],
  health: LocalLLMHealth | null,
  setupPlan: LocalModelSetupPlan | null
): PrivacyReadinessStep[] {
  const readiness = health?.readiness;
  const hasLocalModel = Boolean(health?.available);
  const hardwareReady = readiness?.canInstall ?? true;
  const backend = health?.selectedBackend;
  const setupSteps: PrivacyReadinessStep[] = setupPlan?.steps?.length
    ? setupPlan.steps.map((step) => ({
        key: `setup-${step.key}`,
        label: zhLocalModelSetupStepLabel(step.key, step.label),
        detail: zhLocalModelSetupDetail(step.key, step.detail, setupPlan.model, step.state),
        state: toPrivacyReadinessStepState(step.state)
      }))
    : [];
  const fallbackSetupSteps: PrivacyReadinessStep[] = [
    {
      key: "hardware",
      label: "检查电脑条件",
      detail: readiness?.reason || "会检查内存、磁盘空间和 CPU 是否适合本地模型。",
      state: hardwareReady ? (mode === "efficiency" ? "current" : "done") : "blocked"
    },
    {
      key: "runtime",
      label: "准备本地 AI",
      detail: backend ? `已连接本机模型${backend.model ? `：${modelDisplayName(backend.model)}` : ""}` : "可一键安装本地 AI 应用和推荐模型，也可以连接已有的本机模型服务。",
      state: hasLocalModel ? "done" : hardwareReady ? "current" : "blocked"
    }
  ];

  return [
    {
      key: "mode",
      label: "选择隐私模式",
      detail: mode === "efficiency" ? "当前还在高效模式；开启后会关闭云端辅助和文件上传。" : `${displayMode(mode)}模式已启用。`,
      state: mode === "efficiency" ? "current" : "done"
    },
    ...(setupSteps.length ? setupSteps : fallbackSetupSteps),
    {
      key: "private-tasks",
      label: "开始本地任务",
      detail: hasLocalModel ? "现在可以在隐私模式下处理本地文件和文档问题。" : "本地 AI 就绪后，隐私任务会直接使用本机模型。",
      state: hasLocalModel ? "done" : "current"
    }
  ];
}

function toPrivacyReadinessStepState(state: LocalModelSetupPlan["steps"][number]["state"]): PrivacyReadinessStepState {
  if (state === "done" || state === "current" || state === "blocked") return state;
  return "pending";
}

function privacyReadinessSummary(
  mode: AppSettings["mode"],
  health: LocalLLMHealth | null,
  setupPlan: LocalModelSetupPlan | null,
  checking: boolean
): string {
  if (checking) return "正在确认本地模型、本地 AI 应用和电脑条件。";
  if (mode === "efficiency") return "对标开箱即用体验：一键切换后，Lengrvis 会关闭云端辅助并检查本地 AI。";
  if (health?.available || setupPlan?.ready) return "本地 AI 已可用，隐私任务会优先留在这台电脑上完成。";
  if (setupPlan?.nextAction === "install_runtime") return "这台电脑条件已通过，下一步安装本地 AI 应用。";
  if (setupPlan?.nextAction === "start_runtime") return "本地 AI 已安装，下一步启动本地服务。";
  if (setupPlan?.nextAction === "use_bundled_model") return `${setupPlan.model || "推荐模型"} 已随安装包提供，下一步启用随包模型，无需下载。`;
  if (setupPlan?.nextAction === "download_model") return `本地服务已运行，下一步联网下载 ${setupPlan.model || "推荐模型"}；模型不包含在主程序安装包内。`;
  if (setupPlan && !setupPlan.canInstall) return "这台电脑暂不满足推荐本地模型条件，可继续使用高效模式。";
  if (health?.readiness && !health.readiness.canInstall) return "这台电脑暂不满足推荐本地模型条件，可继续使用高效模式。";
  return "还需要准备本地 AI。可以用下方按钮一键安装推荐模型。";
}

function privacyReadinessPrimaryAction(
  mode: AppSettings["mode"],
  setupPlan: LocalModelSetupPlan | null,
  ready: boolean,
  blocked: boolean,
  checking: boolean
): { label: string; disabled: boolean; kind: "enable" | "start" | "download" | "bundled" | "blocked" | "working" } | null {
  if (mode === "efficiency" || ready) return null;
  if (checking) return { label: "正在准备本地 AI", disabled: true, kind: "working" };
  if (blocked || setupPlan?.nextAction === "hardware_blocked") {
    return { label: "电脑条件暂不满足", disabled: true, kind: "blocked" };
  }
  if (setupPlan?.nextAction === "install_runtime") {
    return { label: `一键安装本地 AI 应用并准备 ${setupPlan.model || "推荐模型"}`, disabled: false, kind: "download" };
  }
  if (setupPlan?.nextAction === "start_runtime" && setupPlan.bundledModelAvailable) {
    return { label: "一键启动本地 AI 服务并启用随包模型", disabled: false, kind: "bundled" };
  }
  if (setupPlan?.nextAction === "start_runtime") {
    return { label: "一键启动本地 AI 服务并检查模型", disabled: false, kind: "start" };
  }
  if (setupPlan?.nextAction === "use_bundled_model") {
    return { label: "一键启用随包模型", disabled: false, kind: "bundled" };
  }
  if (setupPlan?.nextAction === "download_model") {
    return { label: `一键联网下载 ${setupPlan.model || "推荐模型"}`, disabled: false, kind: "download" };
  }
  return { label: "一键准备本地 AI", disabled: false, kind: "enable" };
}

function privacyReadinessPrimaryIcon(
  kind: ReturnType<typeof privacyReadinessPrimaryAction> extends infer T
    ? T extends { kind: infer K }
      ? K
      : undefined
    : undefined,
  checking: boolean
): typeof Loader2 {
  if (checking || kind === "working") return Loader2;
  if (kind === "start") return Play;
  if (kind === "bundled" || kind === "enable") return ShieldCheck;
  if (kind === "blocked") return AlertCircle;
  return Download;
}

function privacyReadinessRepairNote(
  mode: AppSettings["mode"],
  health: LocalLLMHealth | null,
  setupPlan: LocalModelSetupPlan | null,
  ready: boolean,
  checking: boolean
): string {
  if (checking || mode === "efficiency" || ready) return "";
  const model = setupPlan?.model || health?.readiness?.recommendedModel || health?.selectedBackend?.model || "推荐模型";
  const action = setupPlan?.repairAction?.code || setupPlan?.nextAction || "";
  const normalizedAction = action === "free_resources_for_local_ai" ? "hardware_blocked" : action;

  if (normalizedAction === "hardware_blocked" || setupPlan?.canInstall === false || health?.readiness?.canInstall === false) {
    return `阻塞原因：这台电脑暂不满足 ${model} 的推荐条件。释放内存或磁盘后点“重新检查”；隐私任务会等待本地 AI 就绪，不会静默回云端。`;
  }
  if (normalizedAction === "start_runtime") {
    return `下一步说明：本地 AI 应用已准备，但本地服务还没有响应。点击主按钮只会启动本机服务并检查 ${model}，不会上传文件。`;
  }
  if (normalizedAction === "use_bundled_model" || normalizedAction === "restart_runtime_with_bundled_models") {
    return `下一步说明：只有检测到随包资源后才会启用 ${model}；点击主按钮只读取本机资源，不把缺失模型当作已就绪。`;
  }
  if (normalizedAction === "download_model") {
    return `下一步说明：本地 AI 服务已运行，但 ${model} 还没在模型列表中。点击主按钮会下载到这台电脑；完成前隐私任务会等待。`;
  }
  return `下一步说明：当前未检测到可用的本地 AI 应用或随包模型。点击主按钮会在这台电脑上准备 ${model}，不会上传文件，也不会把缺失的离线模型当作已可用。`;
}

function PrivacyBundleStatus({ setupPlan }: { setupPlan: LocalModelSetupPlan }) {
  const manifest = setupPlan.bundleManifest;
  const model = manifest.model || setupPlan.model || "推荐模型";
  const runtimeOk = setupPlan.bundledRuntimeAvailable;
  const modelsOk = setupPlan.bundledModelsAvailable;
  const modelOk = setupPlan.bundledModelAvailable;
  const manifestOk = manifest.present && manifest.valid !== false;
  const manifestText = !manifest.present
    ? "资源清单未找到"
    : manifest.valid === false
      ? "资源清单需要重新校验"
      : `资源清单已校验${manifest.modelsFiles ? ` · ${manifest.modelsFiles} 个模型文件` : ""}`;
  return (
    <div className="privacy-bundle-status" aria-label="随包本地 AI 资源状态">
      <span className={runtimeOk ? "privacy-bundle-status__item privacy-bundle-status__item--ok" : "privacy-bundle-status__item privacy-bundle-status__item--warn"}>
        {runtimeOk ? <CheckCircle2 size={14} aria-hidden="true" /> : <AlertCircle size={14} aria-hidden="true" />}
        本地 AI 应用 {runtimeOk ? "已随包" : "待安装"}
      </span>
      <span className={modelsOk ? "privacy-bundle-status__item privacy-bundle-status__item--ok" : "privacy-bundle-status__item privacy-bundle-status__item--warn"}>
        {modelsOk ? <CheckCircle2 size={14} aria-hidden="true" /> : <AlertCircle size={14} aria-hidden="true" />}
        模型库 {modelsOk ? "已随包" : "未随包"}
      </span>
      <span className={modelOk ? "privacy-bundle-status__item privacy-bundle-status__item--ok" : "privacy-bundle-status__item privacy-bundle-status__item--warn"}>
        {modelOk ? <CheckCircle2 size={14} aria-hidden="true" /> : <AlertCircle size={14} aria-hidden="true" />}
        推荐模型 {modelOk ? model : `${model} 未找到`}
      </span>
      <span className={manifestOk ? "privacy-bundle-status__item privacy-bundle-status__item--ok" : "privacy-bundle-status__item privacy-bundle-status__item--warn"}>
        {manifestOk ? <CheckCircle2 size={14} aria-hidden="true" /> : <AlertCircle size={14} aria-hidden="true" />}
        {manifestText}
      </span>
    </div>
  );
}

function LocalModelEvidenceSummary({ setupPlan }: { setupPlan: LocalModelSetupPlan }) {
  const evidenceCount = setupPlan.evidence.length;
  const failedCount = setupPlan.evidence.filter((item) => !item.ok).length;
  const verification = setupPlan.verification;
  const redaction = verification?.pathsRedacted === true ? "本机路径已隐藏" : "本机路径待隐藏";
  const status = verification?.ready === true
    ? "本地 AI 已验证可用"
    : setupPlan.ready
      ? "本地 AI 可用，等待验证摘要"
      : `下一步：${zhLocalModelRepairAction(setupPlan)}`;
  return (
    <div className="local-model-evidence-summary" aria-label="本地 AI 检查摘要">
      <span>{redaction}</span>
      <span>{status}</span>
      <span>{evidenceCount ? `${evidenceCount} 个检查项，${failedCount} 个待处理` : "等待本地检查结果"}</span>
    </div>
  );
}

function zhLocalModelRepairAction(setupPlan: LocalModelSetupPlan): string {
  const repairCode = setupPlan.repairAction?.code || setupPlan.nextAction;
  const action = repairCode === "free_resources_for_local_ai" ? "hardware_blocked" : repairCode;

  if (action === "hardware_blocked") return "释放内存或磁盘后重新检查；隐私模式不会静默回云端";
  if (action === "continue_setup") return "继续检查本地 AI 应用和模型";
  if (action === "install_runtime") return "安装本地 AI 应用";
  if (action === "start_runtime") return "启动本地 AI 服务";
  if (action === "restart_runtime_with_bundled_models") return "重启本地服务并读取随包模型";
  if (action === "use_bundled_model") return "启用随包模型";
  if (action === "download_model") return `联网下载 ${setupPlan.model || "推荐模型"} 到本机`;
  if (action === "none" || action === "ready") return "本地 AI 已就绪";
  if (action === "prepare_local_ai") return "继续准备本地 AI";
  return zhLocalModelAction(setupPlan.nextAction);
}

function zhLocalModelAction(action: string): string {
  if (action === "hardware_blocked") return "释放内存或磁盘后重试";
  if (action === "install_runtime") return "安装本地 AI 应用";
  if (action === "start_runtime") return "启动本地 AI 服务";
  if (action === "use_bundled_model") return "启用随包模型";
  if (action === "download_model") return "联网下载推荐模型";
  if (action === "ready") return "本地 AI 已就绪";
  if (action === "restart_runtime_with_bundled_models") return "用随包模型重启本地服务";
  return "继续本地 AI 设置";
}

function zhLocalModelSetupStepLabel(key: string, fallback: string): string {
  if (key === "hardware") return "检查电脑条件";
  if (key === "runtime") return "安装本地 AI 应用";
  if (key === "server") return "启动本地 AI 服务";
  if (key === "model") {
    const normalized = fallback.toLowerCase();
    if (normalized.includes("bundled")) return "启用随包模型";
    if (normalized.includes("use local")) return "确认本地模型";
    return "联网下载推荐模型";
  }
  return fallback || "准备本地 AI";
}

function zhLocalModelSetupDetail(
  key: string,
  fallback: string,
  model: string,
  state: LocalModelSetupPlan["steps"][number]["state"]
): string {
  if (key === "hardware") {
    if (state === "done") return `这台电脑已满足 ${model || "推荐模型"} 的本地运行条件。`;
    if (state === "blocked") return "这台电脑暂不满足推荐本地模型条件，可继续使用高效模式。";
    return localModelUserMessage(fallback, "会检查内存、磁盘空间和 CPU 是否适合本地模型。");
  }
  if (key === "runtime" && fallback.includes("bundled")) {
    return state === "done" ? "随包本地 AI 应用已可用。" : "将使用随包本地 AI 应用，无需另外安装。";
  }
  if (key === "runtime") return state === "done" ? "本地 AI 应用已安装。" : "Lengrvis 可以在这台电脑上自动安装本地 AI 应用。";
  if (key === "server") return state === "done" ? "本地 AI 服务正在运行。" : "安装完成后，Lengrvis 会启动本地 AI 服务。";
  if (key === "model" && fallback.includes("included with Lengrvis")) return `${model || "推荐模型"} 已随安装包提供，服务启动后会直接读取。`;
  if (key === "model" && state === "current" && fallback.includes("bundled")) return `${model || "推荐模型"} 已随安装包提供，启用后无需下载。`;
  if (key === "model") return state === "done" ? `${model || "推荐模型"} 已就绪。` : `联网下载 ${model || "推荐模型"} 后即可进入隐私任务；下载文件不计入主程序安装包体积。`;
  return localModelUserMessage(fallback, "继续准备本地 AI。");
}

function LocalModelReadinessView({ readiness }: { readiness: LocalModelReadiness }) {
  return (
    <div className={readiness.canInstall ? "local-model-readiness local-model-readiness--ready" : "local-model-readiness local-model-readiness--blocked"}>
      <div className="local-model-readiness__summary">
        {readiness.canInstall ? <CheckCircle2 size={16} aria-hidden="true" /> : <AlertCircle size={16} aria-hidden="true" />}
        <span>{localModelUserMessage(readiness.reason, "正在检查这台电脑是否适合运行本地 AI。")}</span>
      </div>
      <div className="local-model-readiness__checks">
        {readiness.checks.map((check) => (
          <span key={check.key} className={check.ok ? "local-model-readiness__check local-model-readiness__check--ok" : "local-model-readiness__check local-model-readiness__check--blocked"}>
            <strong>{check.label}</strong>
            {check.actual} / 需要 {check.required}
          </span>
        ))}
      </div>
      {readiness.gpuSummary ? <small>图形加速：{localModelUserMessage(readiness.gpuSummary, "未检测到独立加速硬件；可先使用 CPU 路径。")}</small> : null}
    </div>
  );
}

function installModelStartStage(setupPlan: LocalModelSetupPlan | null, model: string): string {
  const target = setupPlanActionModel(setupPlan, model);
  if (setupPlan?.nextAction === "hardware_blocked") return "电脑条件暂不满足，本次不会继续安装。";
  if (setupPlan?.nextAction === "install_runtime") return `正在一键准备 ${target}：安装本地 AI 应用、启动服务、准备模型。`;
  if (setupPlan?.nextAction === "start_runtime") return `正在启动本地 AI 服务，并检查 ${target} 是否已可用。`;
  if (setupPlan?.nextAction === "use_bundled_model") return `正在启用随包模型 ${target}，无需下载。`;
  if (setupPlan?.nextAction === "download_model") return `正在下载 ${target} 到这台电脑。`;
  if (setupPlan?.nextAction === "ready") return `${target} 已就绪，正在复查本地 AI 状态。`;
  return `正在一键准备 ${target}：检查本地 AI 应用、启动服务、下载或启用模型。`;
}

function localModelInstallerHint(setupPlan: LocalModelSetupPlan | null, model: string): string {
  const target = setupPlanActionModel(setupPlan, model);
  if (!setupPlan) return `上方按钮会按顺序检查本地 AI 应用、启动服务，并准备 ${target}。`;
  if (setupPlan.nextAction === "hardware_blocked") return "电脑条件不足时不会继续安装；释放内存或磁盘后可重新检查。";
  if (setupPlan.nextAction === "install_runtime") return `上方按钮会安装本地 AI 应用、启动服务、准备 ${target}。`;
  if (setupPlan.nextAction === "start_runtime") return `上方按钮会启动本地 AI 服务，然后继续准备 ${target}。`;
  if (setupPlan.nextAction === "use_bundled_model") return `${target} 已随安装包提供，上方按钮会启用它，不走下载。`;
  if (setupPlan.nextAction === "download_model") return `本地 AI 服务已运行，上方按钮会把 ${target} 下载到这台电脑。`;
  if (setupPlan.nextAction === "ready") return `${target} 已就绪；需要换模型时，可在这里手动选择。`;
  return `上方按钮会按当前下一步自动处理 ${target}。`;
}

function setupPlanActionModel(setupPlan: LocalModelSetupPlan | null, model: string): string {
  return setupPlan?.model || model || "推荐模型";
}

function zhInstallModelStatus(status: InstallModelStatus, socketStatus: InstallModelSocketStatus) {
  if (status === "completed") return "已完成";
  if (status === "error") return "安装失败";
  if (status === "installing") {
    if (socketStatus === "connected") return "正在更新";
    if (socketStatus === "reconnecting") return "正在恢复";
    return "安装中";
  }
  return "待安装";
}

interface InstallModelProgressSocketHandlers {
  onOpen: () => void;
  onMessage: (data: unknown) => void;
  onError: () => void;
  onClose: () => void;
}

function subscribeInstallModelProgressSocket(
  baseUrl: string,
  path: string,
  model: string,
  handlers: InstallModelProgressSocketHandlers
): (() => void) | null {
  if (window.lengrvis?.realtime) {
    return null;
  }

  if (typeof WebSocket === "undefined") {
    return null;
  }

  if (!isInstallModelWebOnlyDevFallbackEnabled()) {
    return null;
  }

  const url = buildInstallModelWebSocketUrl(baseUrl, path, model);
  if (!url) return null;
  return subscribeInstallModelWebOnlyDevSocket(url, handlers);
}

function isInstallModelWebOnlyDevFallbackEnabled(): boolean {
  return !window.lengrvis && import.meta.env.DEV;
}

function subscribeInstallModelWebOnlyDevSocket(
  url: string,
  handlers: InstallModelProgressSocketHandlers
): () => void {
  const socket = new WebSocket(url);

  socket.onopen = handlers.onOpen;
  socket.onmessage = (event) => {
    handlers.onMessage(event.data);
  };
  socket.onerror = handlers.onError;
  socket.onclose = handlers.onClose;

  return () => {
    socket.close();
  };
}

function buildInstallModelWebSocketUrl(baseUrl: string, path: string, model: string): string | null {
  return buildRendererLoopbackBackendWebSocketUrl(baseUrl, path, { model });
}

function parseInstallModelProgress(data: unknown): InstallModelProgress | null {
  try {
    const payload = typeof data === "string" ? JSON.parse(data) : data;
    return latestInstallModelProgress(payload);
  } catch {
    return null;
  }
}

function latestInstallModelProgress(payload: unknown): InstallModelProgress | null {
  if (Array.isArray(payload)) {
    for (let index = payload.length - 1; index >= 0; index -= 1) {
      const item = latestInstallModelProgress(payload[index]);
      if (item) return item;
    }
    return null;
  }

  if (!payload || typeof payload !== "object") {
    return null;
  }

  const direct = payload as { final?: unknown; progress?: unknown };
  const finalProgress = latestInstallModelProgress(direct.final);
  if (finalProgress) return finalProgress;

  const nestedProgress = latestInstallModelProgress(direct.progress);
  if (nestedProgress) return nestedProgress;

  return readInstallModelProgress(payload);
}

function readInstallModelProgress(payload: unknown): InstallModelProgress | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const direct = payload as Partial<InstallModelProgress> & {
    message?: unknown;
    status?: unknown;
    phase?: unknown;
    model?: unknown;
  };

  const hasStage = typeof direct.stage === "string" || typeof direct.message === "string";
  const hasPercent = typeof direct.percent === "number";
  const status = typeof direct.status === "string" ? direct.status : "";
  if (!hasStage && !hasPercent && typeof direct.error !== "string" && !status) {
    return null;
  }

  const phase = typeof direct.phase === "string" ? direct.phase : "";
  const model = typeof direct.model === "string" ? direct.model : "";
  const message = typeof direct.message === "string" ? direct.message : "";
  const stage = typeof direct.stage === "string"
    ? direct.stage
    : installModelStatusLabel(status, phase, model, message);

  return normalizeInstallModelProgress({
    stage,
    percent: typeof direct.percent === "number" ? direct.percent : installModelStatusPercent(status, phase),
    error: typeof direct.error === "string" ? direct.error : undefined
  });
}

function installModelStatusLabel(status: string, phase: string, model = "", message = ""): string {
  const target = model || "推荐模型";
  const readableMessage = localModelUserMessage(message, "");
  if (status === "error") return readableMessage ? `安装失败：${readableMessage}` : "安装失败";
  if (phase === "hardware") {
    if (status === "done" || status === "success") return readableMessage || "电脑条件已通过。";
    return readableMessage || "正在检查电脑条件。";
  }
  if (phase === "install") {
    if (status === "done") return "本地 AI 应用已安装。";
    if (status === "skipped") return "本地 AI 应用已安装，继续下一步。";
    return readableMessage || "正在安装本地 AI 应用...";
  }
  if (phase === "start") {
    if (status === "done") return "本地 AI 服务已启动。";
    if (status === "waiting") return "正在等待本地 AI 服务响应...";
    if (status === "starting") return "正在启动本地 AI 服务...";
    return readableMessage || "正在检查本地 AI 服务。";
  }
  if (phase === "pull") {
    if (status === "skipped" && /bundled|随包/i.test(message)) return `${target} 已随包提供，无需下载。`;
    if (status === "skipped") return `${target} 已在本机，无需重复下载。`;
    if (status === "success" || status === "done") return `${target} 已下载到本机。`;
    return readableMessage ? `正在下载 ${target}：${readableMessage}` : `正在下载 ${target}...`;
  }
  if (phase === "switch") {
    if (status === "done" || status === "success") return `${target} 已就绪；隐私模式失败时不会静默回退云端。`;
    return `正在确认 ${target} 可用于隐私模式。`;
  }
  if (status === "success" || status === "done") return "本地模型已就绪";
  if (status === "skipped") return readableMessage || "步骤已跳过";
  if (status === "waiting") return "等待本地 AI 服务启动...";
  if (status === "starting") return "正在开始模型安装...";
  if (status === "installing") return "正在安装本地 AI 应用...";
  return readableMessage || "正在安装本地模型...";
}

function installModelStatusPercent(status: string, phase: string): number {
  if ((status === "success" || status === "done") && phase === "switch") return 100;
  if (status === "error") return 0;
  if (phase === "hardware") return status === "done" ? 8 : 4;
  if (phase === "install") return status === "skipped" || status === "done" ? 25 : 12;
  if (phase === "start") return status === "done" ? 35 : 28;
  if (phase === "pull") return status === "success" ? 92 : 42;
  if (status === "starting" || status === "waiting") return 10;
  if (status === "installing") return 20;
  return 0;
}

function normalizeInstallModelProgress(progress: InstallModelProgress): InstallModelProgress {
  return {
    stage: localModelUserMessage(progress.stage, progress.error ? "安装失败" : "正在安装本地模型..."),
    percent: clampPercent(progress.percent),
    ...(progress.error ? { error: localModelUserMessage(progress.error, "安装失败，请重新检查或重试。") } : {})
  };
}

function localModelOptionValue(model?: string): (typeof LOCAL_MODEL_OPTIONS)[number]["value"] {
  const normalized = (model || "").toLowerCase();
  if (normalized.includes("7b")) return "qwen2.5:7b";
  if (normalized.includes("llama3.2")) return "llama3.2:3b";
  return "qwen2.5:3b";
}

function clampPercent(percent: number) {
  if (!Number.isFinite(percent)) return 0;
  return Math.max(0, Math.min(100, Math.round(percent)));
}

function mobilePairingTransportWarning(baseUrl: string): string {
  try {
    const parsed = new URL(baseUrl);
    if (parsed.protocol !== "http:" || isLoopbackHostname(parsed.hostname)) return "";
    return "当前配对地址是局域网 HTTP。手机端会阻断 token 配对，请在电脑端启用 HTTPS/WSS 或受信任证书后重新生成。";
  } catch {
    return "";
  }
}

type MobilePairingTransportTone = "ready" | "warning" | "blocked";

interface MobilePairingTransportSummary {
  tone: MobilePairingTransportTone;
  label: string;
  detail: string;
  origin: string;
  wssPaths: string[];
  fingerprint: string;
  trustNotice: string;
}

function mobilePairingTransportSummary(pairing: MobilePairingCode, baseUrl: string): MobilePairingTransportSummary {
  const parsed = new URL(baseUrl);
  const transport = pairing.transport_security ?? pairing.server.transport_security ?? {};
  const tlsReady = boolTransportValue(transport, "tls_ready") || boolTransportValue(transport, "tls_enabled");
  const status = textTransportValue(transport, "status");
  const fingerprint =
    textTransportValue(transport, "certificate_fingerprint_sha256") || textTransportValue(transport, "fingerprint_sha256");
  const wssPaths = [
    webSocketPathLabel(parsed, "/ws/mobile/approvals"),
    webSocketPathLabel(parsed, "/ws/remote/screen"),
    webSocketPathLabel(parsed, "/ws/remote/input")
  ];
  const https = parsed.protocol === "https:";
  const loopback = isLoopbackHostname(parsed.hostname);
  const trustRequired = pairing.trust_required === true || boolTransportValue(transport, "trust_required") || boolTransportValue(transport, "requires_trust");

  if (https && tlsReady) {
    return {
      tone: "ready",
      label: "HTTPS/WSS 已写入二维码",
      detail: "手机会使用这个局域网 HTTPS 地址，并把审批、远程屏幕和远程输入升级为 WSS。",
      origin: parsed.origin,
      wssPaths,
      fingerprint,
      trustNotice: trustRequired
        ? "Android 首次连接需要信任这张本机证书；请在手机端确认指纹或按系统/应用提示安装信任后再继续。"
        : ""
    };
  }
  if (https) {
    return {
      tone: "warning",
      label: "HTTPS 已配置但证书未就绪",
      detail: status === "https_misconfigured"
        ? "后端报告证书或私钥未通过校验，请重新生成或重新指向证书后再生成二维码。"
        : "当前二维码是 HTTPS，但缺少可确认的 TLS 就绪状态。",
      origin: parsed.origin,
      wssPaths,
      fingerprint,
      trustNotice: "手机端会 fail closed；请先完成证书生成和信任引导。"
    };
  }
  return {
    tone: loopback ? "warning" : "blocked",
    label: loopback ? "仅限本机调试" : "局域网 HTTP 已阻断",
    detail: loopback
      ? "loopback 地址只适合本机或 emulator 调试，正式手机扫码需要真实局域网 HTTPS 地址。"
      : "token 承载的手机配对、审批、远控和输入授权不能走局域网 HTTP/ws。",
    origin: parsed.origin,
    wssPaths,
    fingerprint: "",
    trustNotice: "请用启动器或服务的自动 LAN TLS 生成 HTTPS/WSS 二维码后重试。"
  };
}

function textTransportValue(transport: Record<string, unknown>, key: string): string {
  const value = transport[key];
  return typeof value === "string" ? value.trim() : "";
}

function boolTransportValue(transport: Record<string, unknown>, key: string): boolean {
  const value = transport[key];
  if (typeof value === "boolean") return value;
  if (typeof value === "string") return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
  return false;
}

function webSocketPathLabel(origin: URL, path: string): string {
  const protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${origin.host}${path}`;
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return normalized === "localhost" || normalized === "::1" || normalized.startsWith("127.");
}

function PairingVisualCodeFallback({ code }: { code?: string }) {
  return (
    <div className="mobile-pairing__visual" aria-label="正在生成手机配对二维码">
      <div className="mobile-pairing__code">{code ?? "------"}</div>
      <div className="mobile-pairing__matrix" aria-hidden="true" />
    </div>
  );
}


function splitSettingList(value: string) {
  return value
    .replace(/\n/g, ";")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean);
}

function readableError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}

function updateWorkspaceRoot(current: AppSettings, workspaceRoot: string): AppSettings {
  const existing = current.allowedDirectories?.length
    ? current.allowedDirectories
    : current.workspaceRoot
      ? [current.workspaceRoot]
      : [];
  const nextDirectories = workspaceRoot
    ? [workspaceRoot, ...existing.slice(1).filter((directory) => directory !== workspaceRoot)]
    : existing.slice(1);
  return {
    ...current,
    workspaceRoot,
    allowedDirectories: nextDirectories
  };
}

function mobileDeviceStatusLabel(device: MobileDevice): string {
  if (device.revoked_at || device.status === "revoked") return "已断开";
  if (device.status === "active" || !device.status) return "已连接";
  return device.status;
}

function mobileDevicePermissionChips(device: MobileDevice, remoteDesktopEnabled: boolean): string[] {
  if (device.revoked_at || device.status === "revoked") {
    return [mobileDeviceStatusLabel(device), "无有效权限"];
  }
  const activeGrant = activeRemoteInputGrantForDevice(device);
  return [
    mobileDeviceStatusLabel(device),
    "审批",
    `全局屏幕查看：${remoteDesktopEnabled ? "开" : "关"}`,
    activeGrant ? "远程点击：临时开" : "远程点击：关"
  ];
}

function formatDeviceDate(value?: string): string {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知";
  return date.toLocaleString();
}

function normalizeHardwareRuntime(value: string): string {
  const lowered = value.trim().toLowerCase();
  if (["", "auto"].includes(lowered)) return "";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "WinML";
  if (lowered === "directml" || lowered === "dml") return "DirectML";
  if (lowered === "openvino") return "OpenVINO";
  if (lowered === "cpu") return "CPU";
  return value;
}

function runtimeToProvider(value: HardwareRuntime): string {
  if (value === "winml") return "WinML";
  if (value === "directml") return "DirectML";
  if (value === "openvino") return "OpenVINO";
  if (value === "cpu") return "CPU";
  return "";
}

function providerToRuntime(value: string): HardwareRuntime {
  const lowered = value.trim().toLowerCase();
  if (!lowered) return "auto";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "winml";
  if (lowered === "directml" || lowered === "dml") return "directml";
  if (lowered === "openvino") return "openvino";
  if (lowered === "cpu") return "cpu";
  return "auto";
}

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

function addMcpServer(setDraft: SetDraft) {
  setDraft((current) => ({
    ...current,
    mcpServers: [...current.mcpServers, { name: "", url: "", enabled: true } satisfies McpServerConfig]
  }));
}

function updateMcpServer(setDraft: SetDraft, index: number, patch: Partial<McpServerConfig>) {
  setDraft((current) => ({
    ...current,
    mcpServers: current.mcpServers.map((server, i) => (i === index ? { ...server, ...patch } : server))
  }));
}

function removeMcpServer(setDraft: SetDraft, index: number) {
  setDraft((current) => ({
    ...current,
    mcpServers: current.mcpServers.filter((_, i) => i !== index)
  }));
}
