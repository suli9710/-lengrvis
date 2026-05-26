import { AlertCircle, CheckCircle2, Download, KeyRound, Loader2, Play, Plus, Save, ShieldCheck, Square, Trash2 } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import type {
  AppSettings,
  BackendStatus,
  LLMCostSummary,
  LLMHealthStatus,
  LocalLLMHealth,
  McpServerConfig
} from "../../shared/types";
import type { MavrisApiClient, MobileDevice, MobilePairingCode } from "../lib/apiClient";
import { zhBackendState } from "../lib/zh";
import { Badge, Panel } from "./Panel";

function zhMode(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "效率（云端）";
  if (mode === "hybrid") return "混合";
  return "隐私（需本地 LLM）";
}

const LOCAL_MODEL_OPTIONS = [
  { value: "qwen2.5:3b", label: "Qwen2.5 3B" },
  { value: "qwen2.5:7b", label: "Qwen2.5 7B" },
  { value: "llama3.2:3b", label: "Llama 3.2 3B" }
] as const;

const INSTALL_MODEL_WS_PATHS = ["/ws/settings/install-local-model", "/api/ws/settings/install-local-model"] as const;
const INSTALL_MODEL_WS_RETRY_DELAY_MS = 2_500;
type PermissionEffect = "allow" | "deny";

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
  progress?: InstallModelProgress;
}

type InstallModelStatus = "idle" | "installing" | "completed" | "error";
type InstallModelSocketStatus = "idle" | "connecting" | "connected" | "reconnecting" | "closed";

interface SettingsPanelProps {
  settings: AppSettings;
  backendStatus: BackendStatus;
  localLlmHealth: LocalLLMHealth | null;
  llmHealth: LLMHealthStatus | null;
  llmCostSummary: LLMCostSummary | null;
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

  useEffect(() => {
    setDraft(settings);
  }, [settings]);

  const save = async () => {
    setIsSaving(true);
    await onSave(draft);
    setIsSaving(false);
  };

  const createPairingCode = async () => {
    setIsPairing(true);
    setPairingError("");
    const response = await api.createMobilePairingCode();
    if (response.ok && response.data) {
      setPairing(response.data);
      void refreshPairedDevices();
    } else {
      setPairingError(response.error?.message ?? "Unable to create pairing code");
    }
    setIsPairing(false);
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
    setIsPermissionSaving(false);
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

  return (
    <Panel
      title="设置"
      eyebrow="运行时"
      action={<Badge tone={backendStatus.state === "running" ? "success" : "warning"}>{zhBackendState(backendStatus.state)}</Badge>}
    >
      <div className="settings-grid">
        <fieldset className="mcp-servers">
          <legend>LLM Runtime</legend>
          <div className="settings-grid">
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
            <p className="muted">Active: {llmHealth?.active.provider ?? "N/A"} / {llmHealth?.active.model ?? "N/A"} / {llmHealth?.active.profile.activeBackend ?? "N/A"}</p>
            <p className="muted">Retry: {llmHealth?.retry.maxRetries ?? "N/A"} retries, {llmHealth?.retry.backoffSeconds ?? "N/A"}s backoff, circuit {llmHealth?.retry.circuit.state ?? "N/A"}</p>
            <p className="muted">Cost: {llmCostSummary ? `${llmCostSummary.calls} calls, ${llmCostSummary.totalTokens} tokens, ${llmCostSummary.totalCostUsd === null ? "N/A" : `$${llmCostSummary.totalCostUsd.toFixed(4)}`}` : "N/A"}</p>
          </div>
        </fieldset>
        <label className="field">
          <span>运行模式</span>
          <div className="mode-radio-row">
            {(["privacy", "efficiency", "hybrid"] as const).map((value) => (
              <label key={value} className="mode-radio">
                <input
                  type="radio"
                  name="mavris-mode"
                  value={value}
                  checked={draft.mode === value}
                  onChange={() => setDraft((current) => ({ ...current, mode: value }))}
                />
                <span>{zhMode(value)}</span>
              </label>
            ))}
          </div>
          {draft.mode === "privacy" || draft.mode === "hybrid" ? (
            <LocalLlmHealthNotice health={localLlmHealth} />
          ) : null}
        </label>
        {draft.mode === "privacy" || draft.mode === "hybrid" ? (
          <div style={{ gridColumn: "1 / -1" }}>
            <LocalModelInstaller api={api} apiBaseUrl={draft.apiBaseUrl} />
          </div>
        ) : null}
        <label className="field">
          <span>授权工作区</span>
          <input
            value={draft.workspaceRoot}
            onChange={(event) => setDraft((current) => ({ ...current, workspaceRoot: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>授权应用白名单</span>
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
          <span>ONNX 模型目录</span>
          <input
            value={draft.onnxModelPath}
            onChange={(event) => setDraft((current) => ({ ...current, onnxModelPath: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>ONNX EP</span>
          <select
            value={draft.onnxExecutionProvider}
            onChange={(event) => setDraft((current) => ({ ...current, onnxExecutionProvider: event.target.value }))}
          >
            <option value="">自动</option>
            <option value="DirectML">DirectML</option>
            <option value="OpenVINO">OpenVINO</option>
            <option value="CPU">CPU</option>
          </select>
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
        <label className="field">
          <span>主题</span>
          <select
            value={draft.theme}
            onChange={(event) =>
              setDraft((current) => ({ ...current, theme: event.target.value as AppSettings["theme"] }))
            }
          >
            <option value="system">跟随系统</option>
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </select>
        </label>
        <div className="toggle-list">
          <label>
            <input
              type="checkbox"
              checked={draft.autoStartBackend}
              onChange={(event) =>
                setDraft((current) => ({ ...current, autoStartBackend: event.target.checked }))
              }
            />
            <span>自动启动后端</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.telemetryEnabled}
              onChange={(event) =>
                setDraft((current) => ({ ...current, telemetryEnabled: event.target.checked }))
              }
            />
            <span>遥测</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.compactMode}
              onChange={(event) =>
                setDraft((current) => ({ ...current, compactMode: event.target.checked }))
              }
            />
            <span>紧凑模式</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.allowBrowserNetwork}
              onChange={(event) =>
                setDraft((current) => ({ ...current, allowBrowserNetwork: event.target.checked }))
              }
            />
            <span>浏览器联网</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.remoteDesktopEnabled}
              onChange={(event) =>
                setDraft((current) => ({ ...current, remoteDesktopEnabled: event.target.checked }))
              }
            />
            <span>手机远程桌面控制</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.allowCloudContext}
              onChange={(event) =>
                setDraft((current) => ({ ...current, allowCloudContext: event.target.checked }))
              }
            />
            <span>允许云端推理（混合模式 / 视觉）</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.allowFileContentUpload}
              onChange={(event) =>
                setDraft((current) => ({ ...current, allowFileContentUpload: event.target.checked }))
              }
            />
            <span>允许文件内容上传到云端</span>
          </label>
        </div>
      </div>
      <fieldset className="mcp-servers">
        <legend>MCP 服务器</legend>
        {draft.mcpServers.length === 0 ? (
          <p className="muted">尚未配置 MCP 服务器。添加后会通过 ToolRegistry 暴露 mcp.&lt;name&gt;.&lt;tool&gt; 形式的工具。</p>
        ) : null}
        <ul className="mcp-servers__list">
          {draft.mcpServers.map((server, index) => (
            <li className="mcp-servers__row" key={index}>
              <input
                placeholder="名称（如 firecrawl）"
                value={server.name}
                onChange={(event) => updateMcpServer(setDraft, index, { name: event.target.value })}
              />
              <input
                placeholder="URL（如 http://127.0.0.1:8787/）"
                value={server.url}
                onChange={(event) => updateMcpServer(setDraft, index, { url: event.target.value })}
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
                aria-label="移除 MCP 服务器"
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
        <button type="button" className="button button--ghost" onClick={() => addMcpServer(setDraft)}>
          <Plus size={14} aria-hidden="true" />
          添加 MCP Server
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
      <div className="button-row">
        <button className="button button--secondary" onClick={() => void onStartBackend()}>
          <Play size={16} aria-hidden="true" />
          启动
        </button>
        <button className="button button--secondary" onClick={() => void onStopBackend()}>
          <Square size={16} aria-hidden="true" />
          停止
        </button>
        <button className="button button--primary" onClick={() => void save()} disabled={isSaving}>
          <Save size={16} aria-hidden="true" />
          保存
        </button>
      </div>
      <div className="mobile-pairing">
        <div className="mobile-pairing__copy">
          <strong>手机配对</strong>
          <span>在 Android 伴侣 App 输入同一局域网的服务器地址和一次性配对码。</span>
          {pairing ? (
            <small>
              服务器：http://{pairing.server.host}:{pairing.server.port} · {new Date(pairing.expires_at).toLocaleTimeString()} 过期
            </small>
          ) : null}
          {pairedDevices.length ? (
            <small>已配对：{pairedDevices.map((device) => device.device_name || device.device_id).join("、")}</small>
          ) : (
            <small>暂无已配对设备</small>
          )}
          {pairingError ? <small className="mobile-pairing__error">{pairingError}</small> : null}
        </div>
        <PairingVisualCode code={pairing?.code} />
        <button className="button button--secondary" onClick={() => void createPairingCode()} disabled={isPairing} type="button">
          {isPairing ? <Loader2 size={16} aria-hidden="true" style={{ animation: "dot-spin 1s linear infinite" }} /> : <KeyRound size={16} aria-hidden="true" />}
          生成配对码
        </button>
      </div>
    </Panel>
  );
}

function LocalModelInstaller({ api, apiBaseUrl }: { api: MavrisApiClient; apiBaseUrl: string }) {
  const [model, setModel] = useState<(typeof LOCAL_MODEL_OPTIONS)[number]["value"]>("qwen2.5:3b");
  const [status, setStatus] = useState<InstallModelStatus>("idle");
  const [socketStatus, setSocketStatus] = useState<InstallModelSocketStatus>("idle");
  const [progress, setProgress] = useState<InstallModelProgress>({
    stage: "选择模型后即可安装到本地推理环境。",
    percent: 0
  });
  const closeProgressSocketRef = useRef<() => void>();

  const isInstalling = status === "installing";

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

  const openProgressSocket = useCallback(() => {
    closeProgressSocket();

    if (typeof WebSocket === "undefined") {
      setSocketStatus("closed");
      return;
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
  }, [apiBaseUrl, applyProgress, closeProgressSocket, model]);

  const installModel = async () => {
    setStatus("installing");
    setProgress({ stage: "正在连接安装进度通道...", percent: 0 });
    openProgressSocket();

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
        stage: response.error?.message ?? "安装请求失败，请检查后端连接。",
        percent: 0,
        error: response.error?.message ?? "安装请求失败"
      });
      return;
    }

    if (response.data?.progress) {
      applyProgress(response.data.progress);
    }

    if (response.data?.ok === false || response.data?.error) {
      closeProgressSocket();
      setStatus("error");
      setProgress({
        stage: response.data.error ?? response.data.message ?? "安装任务启动失败。",
        percent: response.data.progress?.percent ?? progress.percent,
        error: response.data.error ?? response.data.message ?? "安装任务启动失败"
      });
      return;
    }

    setProgress((current) =>
      current.percent > 0
        ? current
        : {
            stage: response.data?.message ?? "安装任务已启动，等待后端推送进度...",
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
    <div
      style={{
        display: "grid",
        gap: 12,
        padding: "12px",
        border: "1px solid var(--line-soft)",
        borderRadius: "var(--r-md)",
        background: "var(--surface-soft)"
      }}
    >
      <div style={{ display: "flex", alignItems: "start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ display: "grid", gap: 3, minWidth: 0 }}>
          <strong style={{ color: "var(--text)", fontSize: 13 }}>端侧模型安装</strong>
          <span style={{ color: "var(--muted)", fontSize: 12, lineHeight: 1.45 }}>
            选择模型后由后端安装到本地运行时，进度会通过 WebSocket 实时更新。
          </span>
        </div>
        <Badge tone={tone}>{zhInstallModelStatus(status, socketStatus)}</Badge>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, 1fr) auto", gap: 10, alignItems: "end" }}>
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
          className="button button--primary"
          disabled={isInstalling}
          onClick={() => void installModel()}
          style={{ minWidth: 158 }}
        >
          {isInstalling ? <Loader2 size={16} aria-hidden="true" style={{ animation: "dot-spin 1s linear infinite" }} /> : <Download size={16} aria-hidden="true" />}
          {isInstalling ? "正在安装" : "一键安装本地模型"}
        </button>
      </div>

      <InstallModelProgressBar progress={progress} />
      {progress.error ? (
        <span style={{ color: "var(--red)", fontSize: 12, fontWeight: 700 }}>{progress.error}</span>
      ) : null}
    </div>
  );
}

function InstallModelProgressBar({ progress }: { progress: InstallModelProgress }) {
  const percent = clampPercent(progress.percent);

  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <span style={{ minWidth: 0, color: "var(--text-soft)", fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {progress.stage}
        </span>
        <span style={{ color: "var(--muted)", fontSize: 12, fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>
          {percent}%
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="本地模型安装进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        style={{
          height: 8,
          overflow: "hidden",
          borderRadius: 999,
          border: "1px solid var(--line-soft)",
          background: "var(--surface)"
        }}
      >
        <div
          style={{
            width: `${percent}%`,
            height: "100%",
            borderRadius: 999,
            background: progress.error ? "var(--red)" : "linear-gradient(90deg, var(--brand) 0%, var(--teal) 100%)",
            transition: "width 0.25s var(--ease-out)"
          }}
        />
      </div>
    </div>
  );
}

function zhInstallModelStatus(status: InstallModelStatus, socketStatus: InstallModelSocketStatus) {
  if (status === "completed") return "已完成";
  if (status === "error") return "安装失败";
  if (status === "installing") {
    if (socketStatus === "connected") return "接收进度";
    if (socketStatus === "reconnecting") return "重连进度";
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
    return readInstallModelProgress(payload);
  } catch {
    return null;
  }
}

function readInstallModelProgress(payload: unknown): InstallModelProgress | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const direct = payload as Partial<InstallModelProgress> & { progress?: unknown; message?: unknown };
  if (typeof direct.progress === "object" && direct.progress !== null) {
    return readInstallModelProgress(direct.progress);
  }

  const hasStage = typeof direct.stage === "string" || typeof direct.message === "string";
  const hasPercent = typeof direct.percent === "number";
  if (!hasStage && !hasPercent && typeof direct.error !== "string") {
    return null;
  }

  return normalizeInstallModelProgress({
    stage: typeof direct.stage === "string" ? direct.stage : typeof direct.message === "string" ? direct.message : "正在安装本地模型...",
    percent: typeof direct.percent === "number" ? direct.percent : 0,
    error: typeof direct.error === "string" ? direct.error : undefined
  });
}

function normalizeInstallModelProgress(progress: InstallModelProgress): InstallModelProgress {
  return {
    stage: progress.stage || (progress.error ? "安装失败" : "正在安装本地模型..."),
    percent: clampPercent(progress.percent),
    ...(progress.error ? { error: progress.error } : {})
  };
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
    : health?.error || "正在读取后端本地 LLM 健康状态。";
  const probes = health?.probeOrder.length ? `探测顺序：${health.probeOrder.join(" → ")}` : "探测顺序：Ollama → LM Studio → llama.cpp";

  return (
    <div
      className={`local-llm-status ${
        health?.available ? "local-llm-status--ready" : "local-llm-status--blocked"
      }`}
      role="status"
    >
      <span className="local-llm-status__dot" aria-hidden="true" />
      <span>
        <strong>{health?.available ? "本地 LLM 可用" : health ? "未检测到本地 LLM" : "检查本地 LLM"}</strong>
        <small>{health?.available ? detail : `${detail} 隐私模式不会静默回退 MockProvider。`}</small>
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
      setError("安装请求失败，请检查后端连接。");
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
      setError("模型拉取请求失败，请检查后端连接。");
    } finally {
      setPulling(false);
    }
  };

  if (!ollamaStatus) {
    return (
      <div style={{ marginTop: 8, padding: "8px 12px", fontSize: 13, opacity: 0.7, display: "flex", alignItems: "center", gap: 6 }}>
        <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
        <span>正在检查 Ollama 状态...</span>
      </div>
    );
  }

  // State 1: Not installed
  if (!ollamaStatus.installed) {
    return (
      <div style={{ marginTop: 8, padding: "8px 12px", borderRadius: 6, background: "var(--color-surface, #f5f5f5)", fontSize: 13 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
          <AlertCircle size={14} style={{ color: "var(--color-warning, #e67e22)" }} />
          <strong>Ollama 未安装</strong>
        </div>
        <p style={{ margin: "0 0 8px", opacity: 0.8 }}>
          隐私模式需要本地 LLM 后端。点击下方按钮通过 winget 自动安装 Ollama。
        </p>
        {error ? <p style={{ margin: "0 0 8px", color: "var(--color-error, #e74c3c)", fontSize: 12 }}>{error}</p> : null}
        <button
          type="button"
          className="button button--secondary"
          disabled={installing}
          onClick={() => void handleInstall()}
          style={{ fontSize: 13, padding: "4px 12px", display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          {installing ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Download size={14} />}
          {installing ? "正在安装..." : "一键安装 Ollama"}
        </button>
      </div>
    );
  }

  // State 2: Installed but not running
  if (!ollamaStatus.running) {
    return (
      <div style={{ marginTop: 8, padding: "8px 12px", borderRadius: 6, background: "var(--color-surface, #f5f5f5)", fontSize: 13 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
          <AlertCircle size={14} style={{ color: "var(--color-warning, #e67e22)" }} />
          <strong>Ollama 未运行</strong>
        </div>
        <p style={{ margin: "0 0 8px", opacity: 0.8 }}>
          Ollama 已安装但服务未启动。请启动 Ollama 应用，然后点击刷新。
        </p>
        <button
          type="button"
          className="button button--secondary"
          onClick={() => void fetchStatus()}
          style={{ fontSize: 13, padding: "4px 12px", display: "inline-flex", alignItems: "center", gap: 6 }}
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
      <div style={{ marginTop: 8, padding: "8px 12px", borderRadius: 6, background: "var(--color-surface, #f5f5f5)", fontSize: 13 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
          <AlertCircle size={14} style={{ color: "var(--color-warning, #e67e22)" }} />
          <strong>推荐模型未安装</strong>
        </div>
        <p style={{ margin: "0 0 8px", opacity: 0.8 }}>
          Ollama 运行中，但推荐模型尚未下载。点击下方按钮拉取模型。
        </p>
        {ollamaStatus.models.length > 0 ? (
          <p style={{ margin: "0 0 8px", fontSize: 12, opacity: 0.7 }}>
            已安装模型：{ollamaStatus.models.join("、")}
          </p>
        ) : null}
        {error ? <p style={{ margin: "0 0 8px", color: "var(--color-error, #e74c3c)", fontSize: 12 }}>{error}</p> : null}
        <button
          type="button"
          className="button button--secondary"
          disabled={pulling}
          onClick={() => void handlePull()}
          style={{ fontSize: 13, padding: "4px 12px", display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          {pulling ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Download size={14} />}
          {pulling ? "正在拉取..." : `拉取 ${ollamaStatus.recommended_model ?? "qwen2.5:3b-instruct"}`}
        </button>
      </div>
    );
  }

  // State 4: Everything ready
  return (
    <div style={{ marginTop: 8, padding: "8px 12px", borderRadius: 6, background: "var(--color-surface, #f5f5f5)", fontSize: 13 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
        <CheckCircle2 size={14} style={{ color: "var(--color-success, #27ae60)" }} />
        <strong>本地 LLM 就绪</strong>
      </div>
      <p style={{ margin: 0, fontSize: 12, opacity: 0.7 }}>
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
      <div className="settings-grid">
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
