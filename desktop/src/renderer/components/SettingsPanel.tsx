import { AlertCircle, CheckCircle2, Download, KeyRound, Loader2, Play, Plus, Save, Square, Trash2 } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import { useCallback, useEffect, useState } from "react";

import type { AppSettings, BackendStatus, LocalLLMHealth, McpServerConfig } from "../../shared/types";
import type { MavrisApiClient, MobileDevice, MobilePairingCode } from "../lib/apiClient";
import { zhBackendState } from "../lib/zh";
import { Badge, Panel } from "./Panel";

function zhMode(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "效率（云端）";
  if (mode === "hybrid") return "混合";
  return "隐私（需本地 LLM）";
}

interface SettingsPanelProps {
  settings: AppSettings;
  backendStatus: BackendStatus;
  localLlmHealth: LocalLLMHealth | null;
  onSave: (settings: AppSettings) => Promise<void>;
  onStartBackend: () => Promise<void>;
  onStopBackend: () => Promise<void>;
  api: MavrisApiClient;
}

export function SettingsPanel({
  settings,
  backendStatus,
  localLlmHealth,
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

  useEffect(() => {
    void refreshPairedDevices();
  }, [refreshPairedDevices]);

  return (
    <Panel
      title="设置"
      eyebrow="运行时"
      action={<Badge tone={backendStatus.state === "running" ? "success" : "warning"}>{zhBackendState(backendStatus.state)}</Badge>}
    >
      <div className="settings-grid">
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
          <LocalLlmHealthNotice health={localLlmHealth} />
        </label>
        <label className="field">
          <span>API 地址</span>
          <input
            value={draft.apiBaseUrl}
            onChange={(event) => setDraft((current) => ({ ...current, apiBaseUrl: event.target.value }))}
          />
        </label>
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
