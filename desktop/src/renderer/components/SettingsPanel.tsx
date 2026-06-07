import { AlertCircle, CheckCircle2, Download, KeyRound, Loader2, MousePointer2, Play, Plus, Save, ShieldCheck, Square, Trash2, XCircle } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

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
import type { MavrisApiClient, MobileDevice, MobilePairingCode, RealtimeConnectionStatus, RemoteInputGrant } from "../lib/apiClient";
import { motionAwareScrollBehavior } from "../lib/motion";
import { activeRemoteInputGrantForDevice, mobileDeviceCanReceiveRemoteInputGrant } from "../lib/remoteInputGrant";
import { zhBackendState, zhRealtimeConnectionStatus, zhRealtimeShortStatus } from "../lib/zh";
import { Badge, Panel } from "./Panel";

function zhMode(mode: AppSettings["mode"]): string {
  return displayMode(mode);
}

function displayMode(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "高效";
  if (mode === "hybrid") return "均衡";
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
  if (mode === "efficiency") return "优先使用云端能力，响应最快。";
  if (mode === "hybrid") return "兼顾云端能力与本地隐私控制。";
  return "尽量把 AI 工作留在本机完成。";
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
type PermissionEffect = "allow" | "deny";
type HardwareRuntime = "auto" | "winml" | "directml" | "openvino" | "cpu";

interface PermissionTimeWindow {
  days: number[];
  start: string;
  end: string;
  timezone?: string;
}

interface PermissionRule {
  id: string;
  name: string;
  effect: PermissionEffect;
  tools: string[];
  pathPatterns: string[];
  timeWindows: PermissionTimeWindow[];
  reason: string;
  enabled: boolean;
}

interface PermissionPolicy {
  rules: PermissionRule[];
  updatedAt?: string;
}

interface BackendPermissionPolicy {
  rules?: BackendPermissionRule[];
  updated_at?: string;
}

interface BackendPermissionRule {
  id?: string;
  name?: string;
  effect?: PermissionEffect;
  tool?: string;
  tools?: string[];
  path_pattern?: string;
  path_patterns?: string[];
  time_window?: BackendPermissionTimeWindow | null;
  time_windows?: BackendPermissionTimeWindow[];
  enabled?: boolean;
  reason?: string;
}

interface BackendPermissionTimeWindow {
  days?: number[];
  start?: string;
  end?: string;
  timezone?: string;
}

const DEFAULT_PERMISSION_POLICY: PermissionPolicy = { rules: [] };
const DEFAULT_PERMISSION_RULE_DRAFT = {
  effect: "deny" as PermissionEffect,
  tool: "file.trash",
  pathPattern: "*",
  days: "weekend",
  start: "00:00",
  end: "23:59",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
  reason: "周末禁止删除文件。"
};

interface InstallModelRequest {
  model: string;
}

interface InstallModelProgress {
  stage: string;
  percent: number;
  error?: string;
}

interface InstallModelStartResponse {
  ok?: boolean;
  message?: string;
  error?: string;
  progress?: unknown;
  final?: unknown;
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
  api: MavrisApiClient;
  privacyIntentId?: number;
}

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
      const query = confirmation.ok && confirmation.data?.required && confirmation.data.nonce
        ? { confirmation_nonce: confirmation.data.nonce }
        : undefined;
      const response = await api.request<BackendPermissionPolicy, BackendPermissionRule>({
        endpoint: "/api/settings/permission-policy/rules",
        method: "POST",
        query,
        body: rule
      });
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
    const response = await api.request<{ ok: boolean; policy: BackendPermissionPolicy }>({
      endpoint: `/api/settings/permission-policy/rules/${encodeURIComponent(ruleId)}`,
      method: "DELETE",
      query: confirmation.ok && confirmation.data?.required && confirmation.data.nonce
        ? { confirmation_nonce: confirmation.data.nonce }
        : undefined
    });
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
    try {
      await onSave(nextDraft);
      await refreshLocalLlmHealth();
    } catch (error) {
      setDraft(previousDraft);
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
        <fieldset className="mcp-servers settings-grid__full">
          <legend>基础设置</legend>
          <div className="settings-grid settings-grid--balanced">
            <label className="field settings-grid__full">
              <span>模式</span>
              <div className="mode-radio-row">
                {(["efficiency", "hybrid", "privacy"] as const).map((value) => (
                  <label key={value} className="mode-radio">
                    <input
                      type="radio"
                      name="mavris-mode"
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
                      name="mavris-permission-mode"
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
            <p className="muted">Mavris: {aiStatus}</p>
            <p className="muted">本地 AI: {localAiStatus}</p>
            {realtimeStatusText ? (
              <p className={realtimeStatusProblem ? "settings-status settings-status--error" : "muted"}>
                实时通道：{realtimeStatusText}
              </p>
            ) : null}
          </div>
        </fieldset>
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
                <input value={draft.model} onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))} />
              </label>
              <label className="field">
                <span>审核模型</span>
                <input value={draft.reviewModel} onChange={(event) => setDraft((current) => ({ ...current, reviewModel: event.target.value }))} />
              </label>
              <label className="field">
                <span>接口类型</span>
                <select value={draft.wireApi} onChange={(event) => setDraft((current) => ({ ...current, wireApi: event.target.value as AppSettings["wireApi"] }))}>
                  <option value="chat_completions">chat_completions</option>
                  <option value="responses">responses</option>
                </select>
              </label>
              <label className="field">
                <span>推理强度</span>
                <input value={draft.modelReasoningEffort} onChange={(event) => setDraft((current) => ({ ...current, modelReasoningEffort: event.target.value }))} />
              </label>
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
              <strong>手机配对</strong>
              <span>在 Android 伴侣应用中输入服务器地址和一次性配对码。</span>
              {pairing ? (
                <small>
                  服务器：http://{pairing.server.host}:{pairing.server.port} · {new Date(pairing.expires_at).toLocaleTimeString()} 过期
                </small>
              ) : null}
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
            <PairingVisualCode code={pairing?.code} />
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
  api: MavrisApiClient;
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
    stage: "选择模型后即可安装到本地推理环境。",
    percent: 0
  });
  const closeProgressSocketRef = useRef<() => void>();

  const isInstalling = status === "installing";
  const canInstall = readiness?.canInstall ?? true;
  const lastError = status === "error" ? progress.error || progress.stage : "";

  useEffect(() => {
    if (readiness?.recommendedModel && status === "idle") {
      setModel(localModelOptionValue(readiness.recommendedModel));
    }
  }, [readiness?.recommendedModel, status]);

  const closeProgressSocket = useCallback(() => {
    closeProgressSocketRef.current?.();
    closeProgressSocketRef.current = undefined;
  }, []);

  useEffect(() => closeProgressSocket, [closeProgressSocket]);

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
      }
    },
    [closeProgressSocket, onHealthRefresh]
  );

  const startInstallRequest = useCallback(
    async (fallbackStage?: string) => {
      closeProgressSocket();
      if (fallbackStage) {
        setProgress({ stage: fallbackStage, percent: 1 });
      }

      const response = await api.request<InstallModelStartResponse, InstallModelRequest>({
        endpoint: "/api/settings/install-local-model",
        method: "POST",
        body: { model },
        timeoutMs: 30_000
      });

      if (!response.ok) {
        setStatus("error");
        setProgress({
          stage: response.error?.message ?? "安装请求失败，请确认 Mavris 正在运行。",
          percent: 0,
          error: response.error?.message ?? "安装请求失败"
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
          stage: response.data.error ?? response.data.message ?? "安装任务启动失败。",
          percent: responseProgress ? responsePercent : 0,
          error: response.data.error ?? response.data.message ?? "安装任务启动失败"
        });
        return;
      }

      if (responseProgress && responsePercent >= 100) {
        setSocketStatus("closed");
        setStatus("completed");
        void onHealthRefresh?.();
        return;
      }

      setProgress((current) =>
        current.percent > 0
          ? current
          : {
              stage: response.data?.message ?? "安装任务已启动，正在等待进度...",
              percent: 1
            }
      );
    },
    [api, applyProgress, closeProgressSocket, model, onHealthRefresh]
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
            void startInstallRequest("进度连接不可用，已切换为普通安装请求...");
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
        stage: readiness?.reason ?? "This computer is below the minimum local AI requirements.",
        percent: 0,
        error: readiness?.reason ?? "This computer is below the minimum local AI requirements."
      });
      return;
    }
    setStatus("installing");
    setProgress({ stage: "正在准备安装...", percent: 0 });
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
        setupPlan={setupPlan}
        checking={isInstalling}
        error={lastError}
        onPrimaryAction={() => void installModel()}
        onRefresh={() => void onHealthRefresh?.()}
        disabled={isInstalling || !canInstall}
      />
      <div className="local-model-installer__head">
        <div className="local-model-installer__copy">
          <strong>本地 AI 手动设置</strong>
          <span>
            上方按钮会按当前下一步自动处理；需要换模型时，可在这里手动选择。
          </span>
        </div>
        <Badge tone={tone}>{zhInstallModelStatus(status, socketStatus)}</Badge>
      </div>

      {readiness ? <LocalModelReadinessView readiness={readiness} /> : null}

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
          {isInstalling ? "正在安装" : "手动安装所选模型"}
        </button>
      </div>

      <InstallModelProgressBar progress={progress} />
      {progress.error ? (
        <span className="settings-status settings-status--error" role="alert">{progress.error}</span>
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
      <p className="privacy-readiness__note">隐私模式会优先使用本机模型处理文件名、文档摘要和系统信息；没有本地 AI 时不会静默退回云端。</p>
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
      detail: backend ? `${backend.kind}${backend.model ? ` · ${backend.model}` : ""}` : "可一键安装 Ollama 和推荐模型，也可使用 LM Studio / llama.cpp。",
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
  if (checking) return "正在确认本地模型、运行时和电脑条件。";
  if (mode === "efficiency") return "对标开箱即用体验：一键切换后，Mavris 会关闭云端辅助并检查本地 AI。";
  if (health?.available || setupPlan?.ready) return "本地 AI 已可用，隐私任务会优先留在这台电脑上完成。";
  if (setupPlan?.nextAction === "install_runtime") return "这台电脑条件已通过，下一步安装本地 AI 运行时。";
  if (setupPlan?.nextAction === "start_runtime") return "本地 AI 已安装，下一步启动本地服务。";
  if (setupPlan?.nextAction === "use_bundled_model") return `${setupPlan.model || "推荐模型"} 已随安装包提供，下一步启用随包模型，无需下载。`;
  if (setupPlan?.nextAction === "download_model") return `本地服务已运行，下一步下载 ${setupPlan.model || "推荐模型"}。`;
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
  if (setupPlan?.nextAction === "start_runtime" && setupPlan.bundledModelAvailable) {
    return { label: "下一步：启动随包本地 AI", disabled: false, kind: "bundled" };
  }
  if (setupPlan?.nextAction === "start_runtime") {
    return { label: "下一步：启动本地 AI 服务", disabled: false, kind: "start" };
  }
  if (setupPlan?.nextAction === "use_bundled_model") {
    return { label: "下一步：启用随包模型", disabled: false, kind: "bundled" };
  }
  if (setupPlan?.nextAction === "download_model") {
    return { label: "下一步：下载推荐模型", disabled: false, kind: "download" };
  }
  return { label: "下一步：准备本地 AI", disabled: false, kind: "enable" };
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

function PrivacyBundleStatus({ setupPlan }: { setupPlan: LocalModelSetupPlan }) {
  const manifest = setupPlan.bundleManifest;
  const model = manifest.model || setupPlan.model || "推荐模型";
  const runtimeOk = setupPlan.bundledRuntimeAvailable;
  const modelsOk = setupPlan.bundledModelsAvailable;
  const modelOk = setupPlan.bundledModelAvailable;
  const manifestOk = manifest.present && manifest.valid !== false;
  const manifestText = !manifest.present
    ? "manifest 未找到"
    : manifest.valid === false
      ? `manifest 未通过：${manifest.error || "需要重新校验"}`
      : `manifest 已校验${manifest.modelsFiles ? ` · ${manifest.modelsFiles} 个模型文件` : ""}`;
  return (
    <div className="privacy-bundle-status" aria-label="随包本地 AI 资源状态">
      <span className={runtimeOk ? "privacy-bundle-status__item privacy-bundle-status__item--ok" : "privacy-bundle-status__item privacy-bundle-status__item--warn"}>
        {runtimeOk ? <CheckCircle2 size={14} aria-hidden="true" /> : <AlertCircle size={14} aria-hidden="true" />}
        运行时 {runtimeOk ? "已随包" : "未随包"}
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

function zhLocalModelSetupStepLabel(key: string, fallback: string): string {
  if (key === "hardware") return "检查电脑条件";
  if (key === "runtime") return "准备本地 AI 运行时";
  if (key === "server") return "启动本地 AI 服务";
  if (key === "model") return "下载推荐模型";
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
    return fallback || "会检查内存、磁盘空间和 CPU 是否适合本地模型。";
  }
  if (key === "runtime") return state === "done" ? "Ollama 已安装。" : "Mavris 可以在 Windows 上自动安装 Ollama。";
  if (key === "server") return state === "done" ? "Ollama 服务正在运行。" : "安装完成后，Mavris 会启动本地 AI 服务。";
  if (key === "model" && fallback.includes("included with Mavris")) return `${model || "推荐模型"} 已随安装包提供，服务启动后会直接读取。`;
  if (key === "model" && state === "current" && fallback.includes("bundled")) return `${model || "推荐模型"} 已随安装包提供，启用后无需下载。`;
  if (key === "model") return state === "done" ? `${model || "推荐模型"} 已就绪。` : `下载 ${model || "推荐模型"} 后即可进入隐私任务。`;
  return fallback;
}

function LocalModelReadinessView({ readiness }: { readiness: LocalModelReadiness }) {
  return (
    <div className={readiness.canInstall ? "local-model-readiness local-model-readiness--ready" : "local-model-readiness local-model-readiness--blocked"}>
      <div className="local-model-readiness__summary">
        {readiness.canInstall ? <CheckCircle2 size={16} aria-hidden="true" /> : <AlertCircle size={16} aria-hidden="true" />}
        <span>{readiness.reason}</span>
      </div>
      <div className="local-model-readiness__checks">
        {readiness.checks.map((check) => (
          <span key={check.key} className={check.ok ? "local-model-readiness__check local-model-readiness__check--ok" : "local-model-readiness__check local-model-readiness__check--blocked"}>
            <strong>{check.label}</strong>
            {check.actual} / 需要 {check.required}
          </span>
        ))}
      </div>
      {readiness.gpuSummary ? <small>GPU: {readiness.gpuSummary}</small> : null}
    </div>
  );
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
  if (window.mavris?.realtime) {
    return window.mavris.realtime.subscribe(
      { endpoint: path, query: { model } },
      {
        onOpen: handlers.onOpen,
        onMessage: handlers.onMessage,
        onError: handlers.onError,
        onClose: handlers.onClose
      }
    );
  }

  if (typeof WebSocket === "undefined") {
    return null;
  }

  if (!isInstallModelWebOnlyDevFallbackEnabled()) {
    return null;
  }

  return subscribeInstallModelWebOnlyDevSocket(buildInstallModelWebSocketUrl(baseUrl, path, model), handlers);
}

function isInstallModelWebOnlyDevFallbackEnabled(): boolean {
  return !window.mavris?.realtime && import.meta.env.DEV;
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

function buildInstallModelWebSocketUrl(baseUrl: string, path: string, model: string): string {
  const url = new URL(path, getInstallModelBackendBaseUrl(baseUrl));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("model", model);
  return url.toString();
}

function getInstallModelBackendBaseUrl(baseUrl: string): string {
  const candidate = window.mavris?.backendBaseUrl || baseUrl || "http://127.0.0.1:8000";
  return /^https?:\/\//i.test(candidate) ? candidate : "http://127.0.0.1:8000";
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

  const direct = payload as Partial<InstallModelProgress> & { message?: unknown; status?: unknown; phase?: unknown };

  const hasStage = typeof direct.stage === "string" || typeof direct.message === "string";
  const hasPercent = typeof direct.percent === "number";
  const status = typeof direct.status === "string" ? direct.status : "";
  if (!hasStage && !hasPercent && typeof direct.error !== "string" && !status) {
    return null;
  }

  const phase = typeof direct.phase === "string" ? direct.phase : "";
  const stage = typeof direct.stage === "string"
    ? direct.stage
    : typeof direct.message === "string"
      ? direct.message
      : installModelStatusLabel(status, phase);

  return normalizeInstallModelProgress({
    stage,
    percent: typeof direct.percent === "number" ? direct.percent : installModelStatusPercent(status, phase),
    error: typeof direct.error === "string" ? direct.error : undefined
  });
}

function installModelStatusLabel(status: string, phase: string): string {
  if (status === "success" || status === "done") return phase ? `${phase} 完成` : "本地模型已就绪";
  if (status === "error") return "安装失败";
  if (status === "skipped") return phase ? `${phase} 已跳过` : "步骤已跳过";
  if (status === "waiting") return "等待本地运行时启动...";
  if (status === "starting") return "正在开始模型安装...";
  if (status === "installing") return "正在安装本地运行时...";
  return "正在安装本地模型...";
}

function installModelStatusPercent(status: string, phase: string): number {
  if ((status === "success" || status === "done") && phase === "switch") return 100;
  if (status === "error") return 0;
  if (phase === "install") return status === "skipped" || status === "done" ? 25 : 12;
  if (phase === "start") return status === "done" ? 35 : 28;
  if (phase === "pull") return status === "success" ? 92 : 42;
  if (status === "starting" || status === "waiting") return 10;
  if (status === "installing") return 20;
  return 0;
}

function normalizeInstallModelProgress(progress: InstallModelProgress): InstallModelProgress {
  return {
    stage: progress.stage || (progress.error ? "安装失败" : "正在安装本地模型..."),
    percent: clampPercent(progress.percent),
    ...(progress.error ? { error: progress.error } : {})
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

function PairingVisualCode({ code }: { code?: string }) {
  const normalized = code ?? "------";
  const bits = Array.from({ length: 36 }, (_, index) => {
    const charCode = normalized.charCodeAt(index % normalized.length) || 45;
    return (charCode + index * 7) % 3 !== 0;
  });

  return (
    <div className="mobile-pairing__visual" aria-label={code ? `配对码 ${code}` : "尚未生成配对码"}>
      <div className="mobile-pairing__code">{normalized}</div>
      <div className="mobile-pairing__matrix" aria-hidden="true">
        {bits.map((active, index) => (
          <span key={index} className={active ? "mobile-pairing__cell mobile-pairing__cell--active" : "mobile-pairing__cell"} />
        ))}
      </div>
    </div>
  );
}

function LocalLlmHealthNotice({ health }: { health: LocalLLMHealth | null }) {
  const backend = health?.selectedBackend;
  const detail = backend
    ? `${backend.kind}${backend.model ? ` · ${backend.model}` : ""}`
    : health?.error || "正在检查本地 AI。";
  const probesText = health?.probeOrder.length ? `已检查：${health.probeOrder.join(" -> ")}` : "已检查：Ollama -> LM Studio -> llama.cpp";

  return (
    <div
      className={`local-llm-status ${
        health?.available ? "local-llm-status--ready" : "local-llm-status--blocked"
      }`}
      role="status"
    >
      <span className="local-llm-status__dot" aria-hidden="true" />
      <span>
        <strong>{health?.available ? "本地 AI 已就绪" : health ? "本地 AI 需要配置" : "正在检查本地 AI"}</strong>
        <small>{health?.available ? detail : `${detail} 隐私模式会等待本地 AI 可用后再继续。`}</small>
        <small>{probesText}</small>
      </span>
    </div>
  );
}

interface OllamaStatus {
  installed: boolean;
  running: boolean;
  models: string[];
  recommended_model?: string;
  has_recommended?: boolean;
}

interface HardwareAccelerationCardProps {
  api: MavrisApiClient;
  settings: AppSettings;
  status: HardwareAccelerationStatusPayload | null;
  loading: boolean;
  error: string;
  smokeStatus: string;
  smoke: HardwareAccelerationSmokePayload | null;
  runtime: string;
  onRuntimeChange: (runtime: HardwareRuntime) => void;
  onSmokeStatusChange: Dispatch<SetStateAction<string>>;
  onSmokeChange: Dispatch<SetStateAction<HardwareAccelerationSmokePayload | null>>;
}

function HardwareAccelerationCard({
  api,
  settings,
  status,
  loading,
  error,
  smokeStatus,
  smoke,
  runtime,
  onRuntimeChange,
  onSmokeStatusChange,
  onSmokeChange
}: HardwareAccelerationCardProps) {
  const [runningOperation, setRunningOperation] = useState<HardwareAccelerationSmokePayload["operation"] | "">("");
  const [smokeError, setSmokeError] = useState("");

  const runSmoke = useCallback(async (operation: HardwareAccelerationSmokePayload["operation"]) => {
    setRunningOperation(operation);
    setSmokeError("");
    onSmokeStatusChange(`正在运行 ${hardwareSmokeLabel(operation)}...`);
    const response = await api.runHardwareAccelerationSmoke({
      operation,
      prompt: "用中文说一句来自 Mavris 硬件加速的问候。",
      maxTokens: 16,
      texts: ["Mavris 本地向量模型冒烟测试。"],
      modelPath: status?.modelPath
    });
    if (response.ok && response.data) {
      onSmokeChange(response.data);
      onSmokeStatusChange(response.data.ok ? `${hardwareSmokeLabel(operation)} 就绪。` : response.data.error ?? "冒烟测试不可用。");
      if (response.data.error) {
        setSmokeError(response.data.error);
      }
    } else {
      const message = response.error?.message ?? "硬件冒烟测试失败。";
      setSmokeError(message);
      onSmokeStatusChange(message);
    }
    setRunningOperation("");
  }, [api, onSmokeChange, onSmokeStatusChange, status?.modelPath]);

  const checks = buildHardwareChecks(settings, status, error);
  const statusTone = status?.available ? "success" : error ? "danger" : "warning";

  return (
    <div className="hardware-acceleration">
      <div className="hardware-acceleration__head">
        <div className="hardware-acceleration__copy">
          <strong>硬件加速</strong>
          <span>WinML、DirectML、OpenVINO、OCR、向量模型和 ONNX GenAI 状态。</span>
        </div>
        <Badge tone={statusTone}>{status?.available ? "就绪" : error ? "错误" : loading ? "检查中" : "缺失"}</Badge>
      </div>
      <div className="settings-grid settings-grid--balanced">
        <label className="field">
          <span>运行时选择</span>
          <select
            value={runtime}
            onChange={(event) => onRuntimeChange(providerToRuntime(event.target.value))}
          >
            <option value="auto">自动</option>
            <option value="winml">WinML</option>
            <option value="directml">DirectML</option>
            <option value="openvino">OpenVINO</option>
            <option value="cpu">CPU</option>
          </select>
        </label>
        <label className="field">
          <span>已配置提供方</span>
          <input value={status?.configuredProvider ?? status?.executionProvider ?? ""} readOnly />
        </label>
        <label className="field">
          <span>模型路径</span>
          <input value={status?.modelPath ?? ""} readOnly />
        </label>
        <label className="field">
          <span>运行时包</span>
          <input value={status?.runtimePackage ?? status?.generationRuntime ?? ""} readOnly />
        </label>
      </div>
      <div className="hardware-acceleration__checks">
        {checks.map((check) => (
          <span key={check.key} className={`hardware-check hardware-check--${check.status}`}>
            <strong>{check.label}</strong>
            <small>{check.details ?? check.actual ?? check.required ?? "不可用"}</small>
          </span>
        ))}
      </div>
      {status?.errors?.length || smokeError || smokeStatus ? (
        <div className="settings-status-grid">
          {status?.errors?.length ? <p className="muted">状态：{status.errors.join(" | ")}</p> : null}
          {smokeStatus ? <p className="muted">冒烟测试：{smokeStatus}</p> : null}
          {smoke?.dim ? <p className="muted">向量维度：{smoke.dim}</p> : null}
          {smokeError ? <p className="muted settings-status--error">{smokeError}</p> : null}
        </div>
      ) : null}
      <div className="button-row">
        {(["warmup", "test_generate", "test_embedding", "test_ocr", "test_image_embedding"] as const).map((operation) => (
          <button
            key={operation}
            type="button"
            className="button button--secondary"
            onClick={() => void runSmoke(operation)}
            disabled={Boolean(runningOperation)}
          >
            {runningOperation === operation ? <Loader2 className="settings-spinner" size={14} /> : <Download size={14} />}
            {hardwareSmokeLabel(operation)}
          </button>
        ))}
      </div>
    </div>
  );
}

function buildHardwareChecks(
  settings: AppSettings,
  status: HardwareAccelerationStatusPayload | null,
  error: string
): Array<{ key: string; label: string; status: "ready" | "missing" | "error"; details?: string; actual?: string; required?: string }> {
  const baseStatus: "ready" | "missing" | "error" = status?.available ? "ready" : error ? "error" : "missing";
  const provider = status?.executionProvider || status?.selectedProvider || "";
  const textEmbeddingStatus = componentStatus(status?.textEmbedding, Boolean(settings.onnxEmbeddingModelPath));
  const imageEmbeddingStatus = componentStatus(status?.imageEmbedding, Boolean(settings.onnxImageEmbeddingModelPath));
  const ocrStatus = componentStatus(status?.ocr, Boolean(settings.ocrOpenvinoModelDir));
  return [
    {
      key: "winml",
      label: "WinML",
      status: status?.winml?.available ? "ready" : status?.available ? "missing" : "missing",
      details: status?.winml?.providerAvailable ? "提供方可用" : "提供方缺失",
      actual: status?.winml?.packages?.join(", "),
      required: "onnxruntime_genai_winml"
    },
    {
      key: "llm",
      label: "LLM",
      status: baseStatus,
      details: provider ? `${status?.kind ?? "onnx"} / ${provider}` : "未就绪",
      actual: provider,
      required: status?.configuredProvider ?? "自动"
    },
    {
      key: "text-embedding",
      label: "文本向量",
      status: textEmbeddingStatus,
      details: status?.textEmbedding?.selectedProvider || status?.textEmbedding?.error || settings.onnxEmbeddingModelId
    },
    {
      key: "image-embedding",
      label: "图像向量",
      status: imageEmbeddingStatus,
      details: status?.imageEmbedding?.selectedProvider || status?.imageEmbedding?.error || settings.onnxImageEmbeddingModelId
    },
    {
      key: "ocr",
      label: "OCR",
      status: status?.ocr?.error ? ocrStatus : status?.errors?.length ? "error" : ocrStatus,
      details: status?.ocr?.selectedProvider || status?.ocr?.error || error || settings.ocrLang || "未检查"
    }
  ];
}

function componentStatus(
  component: HardwareAccelerationStatusPayload["textEmbedding"] | undefined,
  configured: boolean
): "ready" | "missing" | "error" {
  if (component?.available) return "ready";
  if (component?.error && configured) return "error";
  return "missing";
}

function hardwareSmokeLabel(operation: HardwareAccelerationSmokePayload["operation"]): string {
  if (operation === "test_generate") return "测试 LLM";
  if (operation === "test_embedding") return "测试文本";
  if (operation === "test_ocr") return "测试 OCR";
  if (operation === "test_image_embedding") return "测试图像";
  return "预热";
}

function OllamaSetup() {
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [installing, setInstalling] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const doRequest = window.mavris
        ? window.mavris.api.request<OllamaStatus>
        : async (req: { endpoint: string }) => {
            const resp = await fetch(`http://127.0.0.1:8000${req.endpoint}`);
            const data = await resp.json();
            return { ok: resp.ok, data } as { ok: true; data: OllamaStatus };
          };
      const resp = await doRequest({ endpoint: "/api/settings/ollama/status" });
      if (resp.ok && resp.data) {
        setOllamaStatus(resp.data);
        setError(null);
      }
    } catch {
      // Status check failed silently — keep previous state
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleInstall = async () => {
    setInstalling(true);
    setError(null);
    try {
      const doRequest = window.mavris
        ? window.mavris.api.request<{ ok: boolean; message?: string; error?: string }>
        : async (req: { endpoint: string; method?: string }) => {
            const resp = await fetch(`http://127.0.0.1:8000${req.endpoint}`, { method: req.method ?? "GET" });
            const data = await resp.json();
            return { ok: resp.ok, data } as { ok: true; data: { ok: boolean; message?: string; error?: string } };
          };
      const resp = await doRequest({ endpoint: "/api/settings/ollama/install", method: "POST" });
      if (resp.ok && resp.data) {
        if (!resp.data.ok) {
          setError(resp.data.error || "安装失败");
        }
      }
      await fetchStatus();
    } catch {
      setError("安装请求失败，请确认 Mavris 正在运行。");
    } finally {
      setInstalling(false);
    }
  };

  const handlePull = async () => {
    setPulling(true);
    setError(null);
    try {
      const doRequest = window.mavris
        ? window.mavris.api.request<{ ok: boolean; model?: string; message?: string; error?: string }, { model?: string }>
        : async (req: { endpoint: string; method?: string; body?: unknown }) => {
            const resp = await fetch(`http://127.0.0.1:8000${req.endpoint}`, {
              method: req.method ?? "GET",
              headers: req.body ? { "Content-Type": "application/json" } : {},
              body: req.body ? JSON.stringify(req.body) : undefined,
            });
            const data = await resp.json();
            return { ok: resp.ok, data } as { ok: true; data: { ok: boolean; model?: string; message?: string; error?: string } };
          };
      const resp = await doRequest({ endpoint: "/api/settings/ollama/pull", method: "POST", body: {} });
      if (resp.ok && resp.data) {
        if (!resp.data.ok) {
          setError(resp.data.error || "模型拉取失败");
        }
      }
      await fetchStatus();
    } catch {
      setError("模型下载失败，请确认 Mavris 正在运行。");
    } finally {
      setPulling(false);
    }
  };

  if (!ollamaStatus) {
    return (
      <div className="ollama-setup ollama-setup--checking">
        <Loader2 className="settings-spinner" size={14} />
        <span>正在检查 Ollama 状态...</span>
      </div>
    );
  }

  // State 1: Not installed
  if (!ollamaStatus.installed) {
    return (
      <div className="ollama-setup">
        <div className="ollama-setup__head">
          <AlertCircle className="ollama-setup__icon ollama-setup__icon--warning" size={14} />
          <strong>Ollama 未安装</strong>
        </div>
        <p>
          隐私模式需要本地 AI 应用。可使用下方按钮自动安装 Ollama。
        </p>
        {error ? <p className="ollama-setup__error">{error}</p> : null}
        <button
          type="button"
          className="button button--secondary ollama-setup__button"
          disabled={installing}
          onClick={() => void handleInstall()}
        >
          {installing ? <Loader2 className="settings-spinner" size={14} /> : <Download size={14} />}
          {installing ? "正在安装..." : "一键安装 Ollama"}
        </button>
      </div>
    );
  }

  // State 2: Installed but not running
  if (!ollamaStatus.running) {
    return (
      <div className="ollama-setup">
        <div className="ollama-setup__head">
          <AlertCircle className="ollama-setup__icon ollama-setup__icon--warning" size={14} />
          <strong>Ollama 未运行</strong>
        </div>
        <p>
          Ollama 已安装但服务未启动。请从开始菜单搜索并打开 Ollama，等待托盘图标出现后点击刷新。
        </p>
        <button
          type="button"
          className="button button--secondary ollama-setup__button"
          onClick={() => void fetchStatus()}
        >
          <Loader2 size={14} />
          刷新状态
        </button>
      </div>
    );
  }

  // State 3: Running but recommended model not pulled
  if (!ollamaStatus.has_recommended) {
    return (
      <div className="ollama-setup">
        <div className="ollama-setup__head">
          <AlertCircle className="ollama-setup__icon ollama-setup__icon--warning" size={14} />
          <strong>推荐模型未安装</strong>
        </div>
        <p>
          Ollama 运行中，但推荐模型尚未下载。点击下方按钮拉取模型。
        </p>
        {ollamaStatus.models.length > 0 ? (
          <p className="ollama-setup__meta">
            已安装模型：{ollamaStatus.models.join("、")}
          </p>
        ) : null}
        {error ? <p className="ollama-setup__error">{error}</p> : null}
        <button
          type="button"
          className="button button--secondary ollama-setup__button"
          disabled={pulling}
          onClick={() => void handlePull()}
        >
          {pulling ? <Loader2 className="settings-spinner" size={14} /> : <Download size={14} />}
          {pulling ? "正在拉取..." : `拉取 ${ollamaStatus.recommended_model ?? "qwen2.5:3b-instruct"}`}
        </button>
      </div>
    );
  }

  // State 4: Everything ready
  return (
    <div className="ollama-setup ollama-setup--ready">
      <div className="ollama-setup__head">
        <CheckCircle2 className="ollama-setup__icon ollama-setup__icon--success" size={14} />
        <strong>本地 AI 已就绪</strong>
      </div>
      <p className="ollama-setup__meta">
        已安装模型：{ollamaStatus.models.join("、")}
      </p>
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

type PermissionRuleDraft = typeof DEFAULT_PERMISSION_RULE_DRAFT;

interface PermissionPolicyEditorProps {
  policy: PermissionPolicy;
  draft: PermissionRuleDraft;
  status: string;
  isSaving: boolean;
  onDraftChange: Dispatch<SetStateAction<PermissionRuleDraft>>;
  onSave: () => void;
  onDelete: (ruleId: string) => void;
}

function PermissionPolicyEditor({
  policy,
  draft,
  status,
  isSaving,
  onDraftChange,
  onSave,
  onDelete
}: PermissionPolicyEditorProps) {
  return (
    <fieldset className="mcp-servers">
      <legend>权限策略</legend>
      <div className="settings-grid settings-grid--balanced">
        <label className="field">
          <span>规则效果</span>
          <select
            value={draft.effect}
            onChange={(event) =>
              onDraftChange((current) => ({ ...current, effect: event.target.value as PermissionEffect }))
            }
          >
            <option value="deny">拒绝</option>
            <option value="allow">允许</option>
          </select>
        </label>
        <label className="field">
          <span>工具</span>
          <input
            value={draft.tool}
            onChange={(event) => onDraftChange((current) => ({ ...current, tool: event.target.value }))}
            placeholder="file.trash"
          />
        </label>
        <label className="field">
          <span>路径模式</span>
          <input
            value={draft.pathPattern}
            onChange={(event) => onDraftChange((current) => ({ ...current, pathPattern: event.target.value }))}
            placeholder="*"
          />
        </label>
        <label className="field">
          <span>日期</span>
          <input
            value={draft.days}
            onChange={(event) => onDraftChange((current) => ({ ...current, days: event.target.value }))}
            placeholder="weekend 或 0,1,2"
          />
        </label>
        <label className="field">
          <span>开始时间</span>
          <input
            type="time"
            value={draft.start}
            onChange={(event) => onDraftChange((current) => ({ ...current, start: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>结束时间</span>
          <input
            type="time"
            value={draft.end}
            onChange={(event) => onDraftChange((current) => ({ ...current, end: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>时区</span>
          <input
            value={draft.timezone}
            onChange={(event) => onDraftChange((current) => ({ ...current, timezone: event.target.value }))}
            placeholder="Asia/Shanghai"
          />
        </label>
        <label className="field">
          <span>原因</span>
          <input
            value={draft.reason}
            onChange={(event) => onDraftChange((current) => ({ ...current, reason: event.target.value }))}
          />
        </label>
      </div>
      <div className="button-row">
        <button className="button button--primary" onClick={onSave} disabled={isSaving} type="button">
          <ShieldCheck size={16} aria-hidden="true" />
          {isSaving ? "保存中" : "保存规则"}
        </button>
        {status ? <span className="muted">{status}</span> : null}
      </div>
      {policy.rules.length === 0 ? (
        <p className="muted">尚未配置权限规则。</p>
      ) : (
        <ul className="mcp-servers__list">
          {policy.rules.map((rule) => (
            <li className="mcp-servers__row" key={rule.id}>
              <span>
                {rule.enabled ? "" : "[已禁用] "}
                {rule.effect === "allow" ? "允许" : "拒绝"} {rule.tools.join(", ") || "*"} 作用于 {rule.pathPatterns.join(", ") || "*"}
                {rule.timeWindows.length ? `，时间：${rule.timeWindows.map(formatTimeWindow).join("; ")}` : ""}
              </span>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => onDelete(rule.id)}
                aria-label={`删除权限规则 ${rule.id}`}
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

function buildPermissionRule(draft: PermissionRuleDraft): BackendPermissionRule {
  return {
    id: `perm_${crypto.randomUUID().replace(/-/g, "")}`,
    name: `${draft.effect} ${draft.tool}`,
    effect: draft.effect,
    tools: [draft.tool.trim() || "*"],
    path_patterns: [draft.pathPattern.trim() || "*"],
    time_windows: [{
      days: parsePermissionDays(draft.days),
      start: draft.start || "00:00",
      end: draft.end || "23:59",
      timezone: draft.timezone.trim()
    }],
    reason: draft.reason.trim(),
    enabled: true
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

function mapPermissionPolicy(policy: BackendPermissionPolicy): PermissionPolicy {
  return {
    rules: (policy.rules ?? []).map(mapPermissionRule),
    updatedAt: policy.updated_at
  };
}

function mapPermissionRule(rule: BackendPermissionRule): PermissionRule {
  const firstWindow = rule.time_window ? [rule.time_window] : [];
  return {
    id: String(rule.id ?? crypto.randomUUID()),
    name: String(rule.name ?? ""),
    effect: rule.effect === "allow" ? "allow" : "deny",
    tools: (rule.tools ?? (rule.tool ? [rule.tool] : [])).map(String),
    pathPatterns: (rule.path_patterns ?? (rule.path_pattern ? [rule.path_pattern] : [])).map(String),
    timeWindows: [...firstWindow, ...(rule.time_windows ?? [])].map((window) => ({
      days: Array.isArray(window.days) ? window.days.map(Number).filter((day) => Number.isInteger(day)) : [],
      start: String(window.start ?? "00:00"),
      end: String(window.end ?? "23:59"),
      timezone: window.timezone ? String(window.timezone) : ""
    })),
    reason: String(rule.reason ?? ""),
    enabled: rule.enabled !== false
  };
}

function parsePermissionDays(value: string): number[] {
  const tokens = splitSettingList(value.replace(/,/g, ";")).map((item) => item.toLowerCase());
  const days = new Set<number>();
  for (const token of tokens) {
    if (token === "weekend") {
      days.add(5);
      days.add(6);
    } else if (token === "weekday") {
      [0, 1, 2, 3, 4].forEach((day) => days.add(day));
    } else if (PERMISSION_DAY_NAMES[token] !== undefined) {
      days.add(PERMISSION_DAY_NAMES[token]);
    } else {
      const numeric = Number(token);
      if (Number.isInteger(numeric) && numeric >= 0 && numeric <= 6) days.add(numeric);
    }
  }
  return Array.from(days).sort();
}

const PERMISSION_DAY_NAMES: Record<string, number> = {
  mon: 0,
  monday: 0,
  tue: 1,
  tuesday: 1,
  wed: 2,
  wednesday: 2,
  thu: 3,
  thursday: 3,
  fri: 4,
  friday: 4,
  sat: 5,
  saturday: 5,
  sun: 6,
  sunday: 6
};

function formatTimeWindow(window: PermissionTimeWindow): string {
  const days = window.days.length ? window.days.join(",") : "每天";
  return `${days} ${window.start}-${window.end}${window.timezone ? ` ${window.timezone}` : ""}`;
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
