import { AlertCircle, CheckCircle2, Download, KeyRound, Loader2, Play, Plus, Save, ShieldCheck, Square, Trash2 } from "lucide-react";
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
  McpServerConfig
} from "../../shared/types";
import type { MavrisApiClient, MobileDevice, MobilePairingCode } from "../lib/apiClient";
import { zhBackendState } from "../lib/zh";
import { Badge, Panel } from "./Panel";

function zhMode(mode: AppSettings["mode"]): string {
  return displayMode(mode);
}

function displayMode(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "Fast";
  if (mode === "hybrid") return "Balanced";
  return "Private";
}

function modeDescription(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "Uses cloud assistance for the quickest responses.";
  if (mode === "hybrid") return "Balances cloud help with local privacy controls.";
  return "Keeps AI work local when possible.";
}

function appStatusLabel(state: BackendStatus["state"]): string {
  if (state === "running") return "Ready";
  if (state === "starting") return "Starting";
  if (state === "error") return "Needs attention";
  return "Unavailable";
}

const LOCAL_MODEL_OPTIONS = [
  { value: "qwen2.5:3b", label: "Qwen2.5 3B" },
  { value: "qwen2.5:7b", label: "Qwen2.5 7B" },
  { value: "llama3.2:3b", label: "Llama 3.2 3B" }
] as const;

const INSTALL_MODEL_WS_PATHS = ["/ws/settings/install-local-model", "/api/ws/settings/install-local-model"] as const;
const INSTALL_MODEL_WS_RETRY_DELAY_MS = 2_500;
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
  reason: "Weekend file deletion is blocked."
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
  localLlmHealth: LocalLLMHealth | null;
  llmHealth: LLMHealthStatus | null;
  llmCostSummary: LLMCostSummary | null;
  hardwareAccelerationStatus?: HardwareAccelerationStatusPayload | null;
  onSave: (settings: AppSettings) => Promise<void>;
  onStartBackend: () => Promise<void>;
  onStopBackend: () => Promise<void>;
  api: MavrisApiClient;
}

export function SettingsPanel({
  settings,
  backendStatus,
  localLlmHealth,
  llmHealth,
  llmCostSummary,
  hardwareAccelerationStatus,
  onSave,
  onStartBackend,
  onStopBackend,
  api
}: SettingsPanelProps) {
  const [draft, setDraft] = useState(settings);
  const [isSaving, setIsSaving] = useState(false);
  const [pairing, setPairing] = useState<MobilePairingCode | null>(null);
  const [pairingError, setPairingError] = useState("");
  const [isPairing, setIsPairing] = useState(false);
  const [pairedDevices, setPairedDevices] = useState<MobileDevice[]>([]);
  const [permissionPolicy, setPermissionPolicy] = useState<PermissionPolicy>(DEFAULT_PERMISSION_POLICY);
  const [permissionDraft, setPermissionDraft] = useState(DEFAULT_PERMISSION_RULE_DRAFT);
  const [permissionStatus, setPermissionStatus] = useState("");
  const [isPermissionSaving, setIsPermissionSaving] = useState(false);
  const [detectedLocalLlmHealth, setDetectedLocalLlmHealth] = useState<LocalLLMHealth | null>(localLlmHealth);
  const [isCheckingLocalLlm, setIsCheckingLocalLlm] = useState(false);
  const [hardwareStatus, setHardwareStatus] = useState<HardwareAccelerationStatusPayload | null>(hardwareAccelerationStatus ?? null);
  const [isCheckingHardware, setIsCheckingHardware] = useState(false);
  const [hardwareStatusError, setHardwareStatusError] = useState("");
  const [hardwareSmokeStatus, setHardwareSmokeStatus] = useState("");
  const [hardwareSmoke, setHardwareSmoke] = useState<HardwareAccelerationSmokePayload | null>(null);
  const [saveError, setSaveError] = useState("");

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
    if (draft.mode === "efficiency" || detectedLocalLlmHealth) return;
    let cancelled = false;
    setIsCheckingLocalLlm(true);
    void api.getLocalLlmHealth().then((response) => {
      if (cancelled) return;
      if (response.ok && response.data) {
        setDetectedLocalLlmHealth(response.data);
      } else {
        setDetectedLocalLlmHealth({
          available: false,
          selectedBackend: null,
          probeOrder: ["onnx", "ollama", "lmstudio", "llamacpp"],
          error: response.error?.message ?? "Unable to check local AI."
        });
      }
    }).finally(() => {
      if (!cancelled) setIsCheckingLocalLlm(false);
    });
    return () => {
      cancelled = true;
    };
  }, [api, detectedLocalLlmHealth, draft.mode]);

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
          error: response.error?.message ?? "Unable to check hardware acceleration."
        });
        setHardwareStatusError(response.error?.message ?? "Unable to check hardware acceleration.");
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
      setSaveError(readableError(error, "Unable to save settings"));
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
        setPairingError(response.error?.message ?? "Unable to create pairing code");
      }
    } catch (error) {
      setPairingError(readableError(error, "Unable to create pairing code"));
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

  const refreshPermissionPolicy = useCallback(async () => {
    const response = await api.request<BackendPermissionPolicy>({ endpoint: "/api/settings/permission-policy" });
    if (response.ok && response.data) {
      setPermissionPolicy(mapPermissionPolicy(response.data));
      setPermissionStatus("");
    } else {
      setPermissionStatus(response.error?.message ?? "Unable to load permission policy");
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
      const response = await api.request<BackendPermissionPolicy, BackendPermissionRule>({
        endpoint: "/api/settings/permission-policy/rules",
        method: "POST",
        body: buildPermissionRule(permissionDraft)
      });
      if (response.ok && response.data) {
        setPermissionPolicy(mapPermissionPolicy(response.data));
        setPermissionStatus("Permission rule saved.");
      } else {
        setPermissionStatus(response.error?.message ?? "Unable to save permission rule");
      }
    } catch (error) {
      setPermissionStatus(readableError(error, "Unable to save permission rule"));
    } finally {
      setIsPermissionSaving(false);
    }
  };

  const deletePermissionRule = async (ruleId: string) => {
    setPermissionStatus("");
    const response = await api.request<{ ok: boolean; policy: BackendPermissionPolicy }>({
      endpoint: `/api/settings/permission-policy/rules/${encodeURIComponent(ruleId)}`,
      method: "DELETE"
    });
    if (response.ok && response.data) {
      setPermissionPolicy(mapPermissionPolicy(response.data.policy));
      setPermissionStatus("Permission rule removed.");
    } else {
      setPermissionStatus(response.error?.message ?? "Unable to remove permission rule");
    }
  };

  const aiStatus = llmHealth
    ? llmHealth.active.available
      ? llmHealth.active.degraded
        ? "Fallback active"
        : "Ready"
      : "Needs setup"
    : "Checking";
  const localAiStatus = draft.mode === "efficiency"
    ? "Off"
    : isCheckingLocalLlm
      ? "Checking"
      : detectedLocalLlmHealth
        ? detectedLocalLlmHealth.available
        ? "Ready"
        : "Needs setup"
      : "Checking";
  const effectiveLocalLlmHealth = draft.mode === "efficiency" ? null : detectedLocalLlmHealth;
  const hardwareRuntime = providerToRuntime(draft.onnxExecutionProvider);
  return (
    <Panel
      title="Settings"
      eyebrow="Preferences"
      action={<Badge tone={backendStatus.state === "running" ? "success" : "warning"}>{appStatusLabel(backendStatus.state)}</Badge>}
    >
      <div className="settings-grid">
        <fieldset className="mcp-servers settings-grid__full">
          <legend>Basics</legend>
          <div className="settings-grid settings-grid--balanced">
            <label className="field settings-grid__full">
              <span>Mode</span>
              <div className="mode-radio-row">
                {(["efficiency", "hybrid", "privacy"] as const).map((value) => (
                  <label key={value} className="mode-radio">
                    <input
                      type="radio"
                      name="mavris-mode"
                      value={value}
                      checked={draft.mode === value}
                      onChange={() => setDraft((current) => ({ ...current, mode: value }))}
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
              <span>Workspace folder</span>
              <input
                value={draft.workspaceRoot}
                onChange={(event) => setDraft((current) => updateWorkspaceRoot(current, event.target.value))}
              />
              {(draft.allowedDirectories?.length ?? 0) > 1 ? (
                <small className="muted">
                  Preserving {Number(draft.allowedDirectories?.length ?? 1) - 1} additional authorized folder(s).
                </small>
              ) : null}
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
              <span>Allow web access</span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={draft.allowCloudContext}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, allowCloudContext: event.target.checked }))
                }
              />
              <span>Allow cloud assistance</span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={draft.allowFileContentUpload}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, allowFileContentUpload: event.target.checked }))
                }
              />
              <span>Allow file contents when needed</span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={draft.remoteDesktopEnabled}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, remoteDesktopEnabled: event.target.checked }))
                }
              />
              <span>Allow phone screen viewing</span>
            </label>
          </div>
          <div className="settings-status-grid">
            <p className="muted">Mavris: {aiStatus}</p>
            <p className="muted">Local AI: {localAiStatus}</p>
          </div>
        </fieldset>
        {draft.mode === "privacy" || draft.mode === "hybrid" ? (
          <div className="settings-grid__full">
            <LocalModelInstaller api={api} apiBaseUrl={draft.apiBaseUrl} readiness={effectiveLocalLlmHealth?.readiness} />
          </div>
        ) : null}

        <details className="mcp-servers settings-grid__full">
          <summary>Advanced settings</summary>
          <fieldset className="mcp-servers">
            <legend>AI connection</legend>
            <div className="settings-grid settings-grid--balanced">
              <label className="field">
                <span>Provider</span>
                <input value={draft.providerName} onChange={(event) => setDraft((current) => ({ ...current, providerName: event.target.value }))} />
              </label>
              <label className="field">
                <span>Model</span>
                <input value={draft.model} onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))} />
              </label>
              <label className="field">
                <span>Review Model</span>
                <input value={draft.reviewModel} onChange={(event) => setDraft((current) => ({ ...current, reviewModel: event.target.value }))} />
              </label>
              <label className="field">
                <span>Wire API</span>
                <select value={draft.wireApi} onChange={(event) => setDraft((current) => ({ ...current, wireApi: event.target.value as AppSettings["wireApi"] }))}>
                  <option value="chat_completions">chat_completions</option>
                  <option value="responses">responses</option>
                </select>
              </label>
              <label className="field">
                <span>Reasoning Effort</span>
                <input value={draft.modelReasoningEffort} onChange={(event) => setDraft((current) => ({ ...current, modelReasoningEffort: event.target.value }))} />
              </label>
              <label className="field">
                <span>Provider Base URL</span>
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
                <span>Requires OpenAI auth</span>
              </label>
              <label className="mcp-servers__toggle">
                <input
                  type="checkbox"
                  checked={draft.disableResponseStorage}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, disableResponseStorage: event.target.checked }))
                  }
                />
                <span>Disable response storage</span>
              </label>
            </div>
            <div className="settings-status-grid">
              <p className="muted">Active: {llmHealth?.active.provider ?? "N/A"} / {llmHealth?.active.model ?? "N/A"} / {llmHealth?.active.profile.activeBackend ?? "N/A"}</p>
              <p className="muted">Cost: {llmCostSummary ? `${llmCostSummary.calls} calls, ${llmCostSummary.totalTokens} tokens, ${llmCostSummary.totalCostUsd === null ? "N/A" : `$${llmCostSummary.totalCostUsd.toFixed(4)}`}` : "N/A"}</p>
              <p className="muted">Runtime: {zhBackendState(backendStatus.state)}</p>
            </div>
          </fieldset>

          <fieldset className="mcp-servers">
            <legend>Generation and reliability</legend>
            <div className="settings-grid settings-grid--balanced">
              <label className="field">
                <span>Temperature</span>
                <input type="number" min={0} max={2} step={0.05} value={draft.temperature} onChange={(event) => setDraft((current) => ({ ...current, temperature: Number(event.target.value) || 0 }))} />
              </label>
              <label className="field">
                <span>Max Tokens</span>
                <input type="number" min={1} step={1} value={draft.maxTokens} onChange={(event) => setDraft((current) => ({ ...current, maxTokens: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
              <label className="field">
                <span>Timeout</span>
                <input type="number" min={1} step={1} value={draft.timeout} onChange={(event) => setDraft((current) => ({ ...current, timeout: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
              <label className="field">
                <span>Retry Count</span>
                <input type="number" min={0} step={1} value={draft.llmApiMaxRetries} onChange={(event) => setDraft((current) => ({ ...current, llmApiMaxRetries: Math.max(0, Number(event.target.value) || 0) }))} />
              </label>
              <label className="field">
                <span>Retry Backoff</span>
                <input type="number" min={0} step={0.05} value={draft.llmApiRetryBackoffSeconds} onChange={(event) => setDraft((current) => ({ ...current, llmApiRetryBackoffSeconds: Math.max(0, Number(event.target.value) || 0) }))} />
              </label>
              <label className="field">
                <span>Circuit Threshold</span>
                <input type="number" min={1} step={1} value={draft.llmApiCircuitFailureThreshold} onChange={(event) => setDraft((current) => ({ ...current, llmApiCircuitFailureThreshold: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
              <label className="field">
                <span>Circuit Cooldown</span>
                <input type="number" min={0} step={1} value={draft.llmApiCircuitCooldownSeconds} onChange={(event) => setDraft((current) => ({ ...current, llmApiCircuitCooldownSeconds: Math.max(0, Number(event.target.value) || 0) }))} />
              </label>
              <label className="field">
                <span>Context Window</span>
                <input type="number" min={1} step={1} value={draft.modelContextWindow} onChange={(event) => setDraft((current) => ({ ...current, modelContextWindow: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
              <label className="field">
                <span>Auto Compact Limit</span>
                <input type="number" min={1} step={1} value={draft.modelAutoCompactTokenLimit} onChange={(event) => setDraft((current) => ({ ...current, modelAutoCompactTokenLimit: Math.max(1, Number(event.target.value) || 1) }))} />
              </label>
            </div>
            <div className="settings-status-grid">
              <p className="muted">Retry: {llmHealth?.retry.maxRetries ?? "N/A"} retries, {llmHealth?.retry.backoffSeconds ?? "N/A"}s backoff, circuit {llmHealth?.retry.circuit.state ?? "N/A"}</p>
            </div>
          </fieldset>

          <fieldset className="mcp-servers">
            <legend>Desktop internals</legend>
            <div className="settings-grid settings-grid--balanced">
              <label className="field">
                <span>Allowed apps</span>
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
                <span>Browser screenshot directory</span>
                <input
                  value={draft.browserScreenshotDir}
                  onChange={(event) => setDraft((current) => ({ ...current, browserScreenshotDir: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>ONNX model path</span>
                <input
                  value={draft.onnxModelPath}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxModelPath: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>ONNX provider</span>
                <select
                  value={draft.onnxExecutionProvider}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      onnxExecutionProvider: normalizeHardwareRuntime(event.target.value)
                    }))
                  }
                >
                  <option value="">Auto</option>
                  <option value="WinML">WinML</option>
                  <option value="DirectML">DirectML</option>
                  <option value="OpenVINO">OpenVINO</option>
                  <option value="CPU">CPU</option>
                </select>
              </label>
              <label className="field">
                <span>ONNX provider preference</span>
                <input
                  value={draft.onnxProviderPreference}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxProviderPreference: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>WinML / DirectML device id</span>
                <input
                  value={draft.onnxDirectmlDeviceId}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxDirectmlDeviceId: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>OpenVINO device</span>
                <input
                  value={draft.onnxOpenvinoDevice}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxOpenvinoDevice: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>OpenVINO cache dir</span>
                <input
                  value={draft.onnxOpenvinoCacheDir}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxOpenvinoCacheDir: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>Warm on startup</span>
                <select
                  value={draft.onnxWarmOnStartup ? "yes" : "no"}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxWarmOnStartup: event.target.value === "yes" }))}
                >
                  <option value="no">No</option>
                  <option value="yes">Yes</option>
                </select>
              </label>
              <label className="field">
                <span>Model family</span>
                <input
                  value={draft.onnxModelFamily}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxModelFamily: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>Embedding backend</span>
                <input
                  value={draft.onnxEmbeddingBackend}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxEmbeddingBackend: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>Embedding model path</span>
                <input
                  value={draft.onnxEmbeddingModelPath}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxEmbeddingModelPath: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>Embedding EP</span>
                <input
                  value={draft.onnxEmbeddingExecutionProvider}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxEmbeddingExecutionProvider: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>Image embedding backend</span>
                <input
                  value={draft.imageEmbeddingBackend}
                  onChange={(event) => setDraft((current) => ({ ...current, imageEmbeddingBackend: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>Image embedding model path</span>
                <input
                  value={draft.onnxImageEmbeddingModelPath}
                  onChange={(event) => setDraft((current) => ({ ...current, onnxImageEmbeddingModelPath: event.target.value }))}
                />
              </label>
              <label className="field">
                <span>OCR backend</span>
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
                <span>Web page read limit</span>
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
            <legend>Tool connections</legend>
            {draft.mcpServers.length === 0 ? (
              <p className="muted">No tool connections configured.</p>
            ) : null}
            <ul className="mcp-servers__list">
              {draft.mcpServers.map((server, index) => (
                <li className="mcp-servers__row mcp-servers__row--server" key={index}>
                  <input
                    placeholder="Name"
                    value={server.name}
                    onChange={(event) => updateMcpServer(setDraft, index, { name: event.target.value })}
                  />
                  <input
                    placeholder="URL"
                    value={server.url}
                    onChange={(event) => updateMcpServer(setDraft, index, { url: event.target.value })}
                  />
                  <input
                    placeholder="Command"
                    value={server.command ?? ""}
                    onChange={(event) => updateMcpServer(setDraft, index, { command: event.target.value })}
                  />
                  <input
                    placeholder="Args"
                    value={server.args?.join("; ") ?? ""}
                    onChange={(event) => updateMcpServer(setDraft, index, { args: splitSettingList(event.target.value) })}
                  />
                  <input
                    placeholder="Transport"
                    value={server.transport ?? ""}
                    onChange={(event) => updateMcpServer(setDraft, index, { transport: event.target.value })}
                  />
                  <label className="mcp-servers__toggle">
                    <input
                      type="checkbox"
                      checked={server.enabled}
                      onChange={(event) => updateMcpServer(setDraft, index, { enabled: event.target.checked })}
                    />
                    <span>Enabled</span>
                  </label>
                  <button
                    type="button"
                    className="button button--ghost"
                    onClick={() => removeMcpServer(setDraft, index)}
                    aria-label="Remove tool connection"
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
            <button type="button" className="button button--ghost" onClick={() => addMcpServer(setDraft)}>
              <Plus size={14} aria-hidden="true" />
              Add tool connection
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
            <legend>Runtime controls</legend>
            <div className="button-row">
              <button className="button button--secondary" onClick={() => void onStartBackend()}>
                <Play size={16} aria-hidden="true" />
                Start
              </button>
              <button className="button button--secondary" onClick={() => void onStopBackend()}>
                <Square size={16} aria-hidden="true" />
                Stop
              </button>
            </div>
          </fieldset>

          <div className="mobile-pairing">
            <div className="mobile-pairing__copy">
              <strong>Phone pairing</strong>
              <span>Enter the server address and one-time pairing code in the Android companion app.</span>
              {pairing ? (
                <small>
                  Server: http://{pairing.server.host}:{pairing.server.port} · expires {new Date(pairing.expires_at).toLocaleTimeString()}
                </small>
              ) : null}
              {pairedDevices.length ? (
                <small>Paired: {pairedDevices.map((device) => device.device_name || device.device_id).join(", ")}</small>
              ) : (
                <small>No paired devices.</small>
              )}
              {pairingError ? <small className="mobile-pairing__error">{pairingError}</small> : null}
            </div>
            <PairingVisualCode code={pairing?.code} />
            <button className="button button--secondary" onClick={() => void createPairingCode()} disabled={isPairing} type="button">
              {isPairing ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />}
              Generate pairing code
            </button>
          </div>
        </details>

        <div className="button-row settings-grid__full">
          <button className="button button--primary" onClick={() => void save()} disabled={isSaving}>
            <Save size={16} aria-hidden="true" />
            {isSaving ? "Saving" : "Save settings"}
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
  readiness
}: {
  api: MavrisApiClient;
  apiBaseUrl: string;
  readiness?: LocalModelReadiness;
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
      }
    },
    [closeProgressSocket]
  );

  const openProgressSocket = useCallback((): boolean => {
    closeProgressSocket();

    if (typeof WebSocket === "undefined") {
      setSocketStatus("closed");
      return false;
    }

    let socket: WebSocket | null = null;
    let closedByCaller = false;
    let retryId: number | undefined;
    let pathIndex = 0;
    let receivedProgress = false;

    const connect = () => {
      setSocketStatus(pathIndex === 0 && !receivedProgress ? "connecting" : "reconnecting");
      socket = new WebSocket(buildInstallModelWebSocketUrl(apiBaseUrl, INSTALL_MODEL_WS_PATHS[pathIndex], model));

      socket.onopen = () => {
        setSocketStatus("connected");
      };
      socket.onmessage = (event) => {
        receivedProgress = true;
        const nextProgress = parseInstallModelProgress(event.data);
        if (nextProgress) {
          applyProgress(nextProgress);
        }
      };
      socket.onerror = () => {
        setSocketStatus("reconnecting");
      };
      socket.onclose = () => {
        socket = null;
        if (closedByCaller) {
          setSocketStatus("closed");
          return;
        }
        if (!receivedProgress && pathIndex < INSTALL_MODEL_WS_PATHS.length - 1) {
          pathIndex += 1;
        }
        retryId = window.setTimeout(connect, INSTALL_MODEL_WS_RETRY_DELAY_MS);
      };
    };

    connect();

    closeProgressSocketRef.current = () => {
      closedByCaller = true;
      if (retryId !== undefined) window.clearTimeout(retryId);
      socket?.close();
      socket = null;
      setSocketStatus("closed");
    };

    return true;
  }, [apiBaseUrl, applyProgress, closeProgressSocket, model]);

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

    const response = await api.request<InstallModelStartResponse, InstallModelRequest>({
      endpoint: "/api/settings/install-local-model",
      method: "POST",
      body: { model },
      timeoutMs: 30_000
    });

    if (!response.ok) {
      closeProgressSocket();
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
      closeProgressSocket();
      setStatus("error");
      setProgress({
        stage: response.data.error ?? response.data.message ?? "安装任务启动失败。",
        percent: responseProgress ? responsePercent : progress.percent,
        error: response.data.error ?? response.data.message ?? "安装任务启动失败"
      });
      return;
    }

    if (responseProgress && responsePercent >= 100) {
      closeProgressSocket();
      setSocketStatus("closed");
      setStatus("completed");
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
      <div className="local-model-installer__head">
        <div className="local-model-installer__copy">
          <strong>Local AI setup</strong>
          <span>
            Mavris checks this computer first, then installs Ollama and a local model when it is ready.
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
          className="button button--primary local-model-installer__button"
          disabled={isInstalling || !canInstall}
          onClick={() => void installModel()}
        >
          {isInstalling ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
          {isInstalling ? "正在安装" : "一键安装本地模型"}
        </button>
      </div>

      <InstallModelProgressBar progress={progress} />
      {progress.error ? (
        <span className="settings-status settings-status--error">{progress.error}</span>
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
            {check.actual} / needs {check.required}
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
  const probes = health?.probeOrder.length ? `Checked: ${health.probeOrder.join(" -> ")}` : "Checked: Ollama -> LM Studio -> llama.cpp";

  return (
    <div
      className={`local-llm-status ${
        health?.available ? "local-llm-status--ready" : "local-llm-status--blocked"
      }`}
      role="status"
    >
      <span className="local-llm-status__dot" aria-hidden="true" />
      <span>
        <strong>{health?.available ? "Local AI is ready" : health ? "Local AI needs setup" : "Checking local AI"}</strong>
        <small>{health?.available ? detail : `${detail} Private mode will wait until local AI is available.`}</small>
        <small>{probes}</small>
      </span>
      {health && !health.available ? <OllamaSetup /> : null}
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
    onSmokeStatusChange(`Running ${hardwareSmokeLabel(operation)}...`);
    const response = await api.runHardwareAccelerationSmoke({
      operation,
      prompt: "Say hello from Mavris hardware acceleration.",
      maxTokens: 16,
      texts: ["Mavris local embedding smoke test."],
      modelPath: status?.modelPath
    });
    if (response.ok && response.data) {
      onSmokeChange(response.data);
      onSmokeStatusChange(response.data.ok ? `${hardwareSmokeLabel(operation)} ready.` : response.data.error ?? "Smoke unavailable.");
      if (response.data.error) {
        setSmokeError(response.data.error);
      }
    } else {
      const message = response.error?.message ?? "Hardware smoke failed.";
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
          <strong>Hardware acceleration</strong>
          <span>WinML, DirectML, OpenVINO, OCR, embeddings, and ONNX GenAI status.</span>
        </div>
        <Badge tone={statusTone}>{status?.available ? "Ready" : error ? "Error" : loading ? "Checking" : "Missing"}</Badge>
      </div>
      <div className="settings-grid settings-grid--balanced">
        <label className="field">
          <span>Runtime selector</span>
          <select
            value={runtime}
            onChange={(event) => onRuntimeChange(providerToRuntime(event.target.value))}
          >
            <option value="auto">Auto</option>
            <option value="winml">WinML</option>
            <option value="directml">DirectML</option>
            <option value="openvino">OpenVINO</option>
            <option value="cpu">CPU</option>
          </select>
        </label>
        <label className="field">
          <span>Configured provider</span>
          <input value={status?.configuredProvider ?? status?.executionProvider ?? ""} readOnly />
        </label>
        <label className="field">
          <span>Model path</span>
          <input value={status?.modelPath ?? ""} readOnly />
        </label>
        <label className="field">
          <span>Runtime package</span>
          <input value={status?.runtimePackage ?? status?.generationRuntime ?? ""} readOnly />
        </label>
      </div>
      <div className="hardware-acceleration__checks">
        {checks.map((check) => (
          <span key={check.key} className={`hardware-check hardware-check--${check.status}`}>
            <strong>{check.label}</strong>
            <small>{check.details ?? check.actual ?? check.required ?? "Unavailable"}</small>
          </span>
        ))}
      </div>
      {status?.errors?.length || smokeError || smokeStatus ? (
        <div className="settings-status-grid">
          {status?.errors?.length ? <p className="muted">Status: {status.errors.join(" | ")}</p> : null}
          {smokeStatus ? <p className="muted">Smoke: {smokeStatus}</p> : null}
          {smoke?.dim ? <p className="muted">Vector dim: {smoke.dim}</p> : null}
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
      details: status?.winml?.providerAvailable ? "Provider available" : "Provider missing",
      actual: status?.winml?.packages?.join(", "),
      required: "onnxruntime_genai_winml"
    },
    {
      key: "llm",
      label: "LLM",
      status: baseStatus,
      details: provider ? `${status?.kind ?? "onnx"} / ${provider}` : "Not ready",
      actual: provider,
      required: status?.configuredProvider ?? "Auto"
    },
    {
      key: "text-embedding",
      label: "Text embedding",
      status: textEmbeddingStatus,
      details: status?.textEmbedding?.selectedProvider || status?.textEmbedding?.error || settings.onnxEmbeddingModelId
    },
    {
      key: "image-embedding",
      label: "Image embedding",
      status: imageEmbeddingStatus,
      details: status?.imageEmbedding?.selectedProvider || status?.imageEmbedding?.error || settings.onnxImageEmbeddingModelId
    },
    {
      key: "ocr",
      label: "OCR",
      status: status?.ocr?.error ? ocrStatus : status?.errors?.length ? "error" : ocrStatus,
      details: status?.ocr?.selectedProvider || status?.ocr?.error || error || settings.ocrLang || "Not checked"
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
  if (operation === "test_generate") return "Test LLM";
  if (operation === "test_embedding") return "Test text";
  if (operation === "test_ocr") return "Test OCR";
  if (operation === "test_image_embedding") return "Test image";
  return "Warm up";
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
          Private mode needs a local AI app. Use the button below to install Ollama automatically.
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
          Ollama 已安装但服务未启动。请启动 Ollama 应用，然后点击刷新。
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
        <strong>Local AI is ready</strong>
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
      <legend>Permission Policy</legend>
      <div className="settings-grid settings-grid--balanced">
        <label className="field">
          <span>Effect</span>
          <select
            value={draft.effect}
            onChange={(event) =>
              onDraftChange((current) => ({ ...current, effect: event.target.value as PermissionEffect }))
            }
          >
            <option value="deny">Deny</option>
            <option value="allow">Allow</option>
          </select>
        </label>
        <label className="field">
          <span>Tool</span>
          <input
            value={draft.tool}
            onChange={(event) => onDraftChange((current) => ({ ...current, tool: event.target.value }))}
            placeholder="file.trash"
          />
        </label>
        <label className="field">
          <span>Path pattern</span>
          <input
            value={draft.pathPattern}
            onChange={(event) => onDraftChange((current) => ({ ...current, pathPattern: event.target.value }))}
            placeholder="*"
          />
        </label>
        <label className="field">
          <span>Days</span>
          <input
            value={draft.days}
            onChange={(event) => onDraftChange((current) => ({ ...current, days: event.target.value }))}
            placeholder="weekend"
          />
        </label>
        <label className="field">
          <span>Start</span>
          <input
            type="time"
            value={draft.start}
            onChange={(event) => onDraftChange((current) => ({ ...current, start: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>End</span>
          <input
            type="time"
            value={draft.end}
            onChange={(event) => onDraftChange((current) => ({ ...current, end: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>Timezone</span>
          <input
            value={draft.timezone}
            onChange={(event) => onDraftChange((current) => ({ ...current, timezone: event.target.value }))}
            placeholder="Asia/Shanghai"
          />
        </label>
        <label className="field">
          <span>Reason</span>
          <input
            value={draft.reason}
            onChange={(event) => onDraftChange((current) => ({ ...current, reason: event.target.value }))}
          />
        </label>
      </div>
      <div className="button-row">
        <button className="button button--primary" onClick={onSave} disabled={isSaving} type="button">
          <ShieldCheck size={16} aria-hidden="true" />
          {isSaving ? "Saving" : "Save Rule"}
        </button>
        {status ? <span className="muted">{status}</span> : null}
      </div>
      {policy.rules.length === 0 ? (
        <p className="muted">No permission rules configured.</p>
      ) : (
        <ul className="mcp-servers__list">
          {policy.rules.map((rule) => (
            <li className="mcp-servers__row" key={rule.id}>
              <span>
                {rule.enabled ? "" : "[disabled] "}
                {rule.effect.toUpperCase()} {rule.tools.join(", ") || "*"} on {rule.pathPatterns.join(", ") || "*"}
                {rule.timeWindows.length ? ` during ${rule.timeWindows.map(formatTimeWindow).join("; ")}` : ""}
              </span>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => onDelete(rule.id)}
                aria-label={`Remove permission rule ${rule.id}`}
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
  const days = window.days.length ? window.days.join(",") : "all days";
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
