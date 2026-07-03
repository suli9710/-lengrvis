import { AlertCircle, CheckCircle2, Download, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import type { ApiMethod, ApiResponse } from "../../../shared/types";
import { buildRendererLoopbackBackendApiUrl } from "../../lib/apiClient";

interface OllamaStatus {
  installed: boolean;
  running: boolean;
  models: string[];
  recommended_model?: string;
  has_recommended?: boolean;
}

interface OllamaActionResult {
  ok: boolean;
  model?: string;
  message?: string;
  error?: string;
}

interface OllamaSetupRequest<TBody = unknown> {
  endpoint: string;
  method?: ApiMethod;
  body?: TBody;
}

async function requestOllamaSetup<TResponse, TBody = unknown>(
  request: OllamaSetupRequest<TBody>
): Promise<ApiResponse<TResponse>> {
  if (window.lengrvis) {
    return window.lengrvis.api.request<TResponse, TBody>({
      endpoint: request.endpoint,
      method: request.method,
      body: request.body
    });
  }
  return requestOllamaSetupDirect<TResponse, TBody>(request);
}

async function requestOllamaSetupDirect<TResponse, TBody = unknown>(
  request: OllamaSetupRequest<TBody>
): Promise<ApiResponse<TResponse>> {
  const receivedAt = new Date().toISOString();
  const url = buildRendererLoopbackBackendApiUrl(undefined, request.endpoint);
  if (!url) {
    return {
      ok: false,
      status: 0,
      error: { message: "Web 调试后端仅允许连接本机 HTTP(S) 地址。" },
      receivedAt
    };
  }

  try {
    const response = await fetch(url, {
      method: request.method ?? "GET",
      headers: request.body ? { "Content-Type": "application/json" } : {},
      body: request.body ? JSON.stringify(request.body) : undefined
    });
    const data = await parseOllamaSetupResponse(response);
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: { message: ollamaSetupErrorMessage(data, response.statusText || `HTTP ${response.status}`), details: data },
        receivedAt
      };
    }
    return { ok: true, status: response.status, data: data as TResponse, receivedAt };
  } catch (error) { // broad-exception-boundary
    return {
      ok: false,
      status: 0,
      error: { message: error instanceof Error ? error.message : "本地 AI 请求失败" },
      receivedAt
    };
  }
}

async function parseOllamaSetupResponse(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

function ollamaSetupErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object") {
    const direct = (data as { message?: unknown }).message;
    if (typeof direct === "string") return direct;
    const error = (data as { error?: unknown }).error;
    if (typeof error === "string") return error;
  }
  return fallback;
}

export function OllamaSetup() {
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [installing, setInstalling] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await requestOllamaSetup<OllamaStatus>({ endpoint: "/api/settings/ollama/status" });
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
      const resp = window.lengrvis?.ollama
        ? await window.lengrvis.ollama.install() as ApiResponse<OllamaActionResult>
        : await requestOllamaSetup<OllamaActionResult>({ endpoint: "/api/settings/ollama/install", method: "POST" });
      if (resp.ok && resp.data) {
        if (!resp.data.ok) {
          setError(resp.data.error || "安装失败");
        }
      }
      await fetchStatus();
    } catch {
      setError("安装请求失败，请确认 Lengrvis 正在运行。");
    } finally {
      setInstalling(false);
    }
  };

  const handlePull = async () => {
    setPulling(true);
    setError(null);
    try {
      const resp = window.lengrvis?.ollama
        ? await window.lengrvis.ollama.pull({}) as ApiResponse<OllamaActionResult>
        : await requestOllamaSetup<OllamaActionResult>({ endpoint: "/api/settings/ollama/pull", method: "POST", body: {} });
      if (resp.ok && resp.data) {
        if (!resp.data.ok) {
          setError(localModelUserMessage(resp.data.error, "模型下载失败"));
        }
      }
      await fetchStatus();
    } catch {
      setError("模型下载失败，请确认 Lengrvis 正在运行。");
    } finally {
      setPulling(false);
    }
  };

  if (!ollamaStatus) {
    return (
      <div className="ollama-setup ollama-setup--checking">
        <Loader2 className="settings-spinner" size={14} />
        <span>正在检查本地 AI 应用状态...</span>
      </div>
    );
  }

  // State 1: Not installed
  if (!ollamaStatus.installed) {
    return (
      <div className="ollama-setup">
        <div className="ollama-setup__head">
          <AlertCircle className="ollama-setup__icon ollama-setup__icon--warning" size={14} />
          <strong>本地 AI 应用未安装</strong>
        </div>
        <p>
          隐私模式需要本地 AI 应用。可使用下方按钮自动安装。
        </p>
        {error ? <p className="ollama-setup__error">{error}</p> : null}
        <button
          type="button"
          className="button button--secondary ollama-setup__button"
          disabled={installing}
          onClick={() => void handleInstall()}
        >
          {installing ? <Loader2 className="settings-spinner" size={14} /> : <Download size={14} />}
          {installing ? "正在安装..." : "一键安装本地 AI 应用"}
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
          <strong>本地 AI 服务未运行</strong>
        </div>
        <p>
          本地 AI 应用已安装但服务未启动。请从开始菜单打开本地 AI 应用，等待托盘图标出现后点击刷新。
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
          本地 AI 服务运行中，但推荐模型尚未下载。点击下方按钮下载模型。
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
          {pulling ? "正在下载..." : `下载 ${ollamaStatus.recommended_model ?? "qwen2.5:3b-instruct"}`}
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


function localModelUserMessage(message?: string, fallback = "继续准备本地 AI。"): string {
  const text = `${message ?? ""}`.trim();
  if (!text) return fallback;
  if (containsSensitiveLocalModelText(text)) return fallback;
  const lower = text.toLowerCase();
  if (lower.includes("privacy mode requires") || lower.includes("reachable local llm")) {
    return "还没有可用的本地 AI。可以在下方一键安装并启动，或先切换到高效模式继续使用。";
  }
  if (lower.includes("not installed") && lower.includes("ollama")) {
    return "本地 AI 应用还未安装。点击「安装」会按这台电脑自动下载，无需手动配置。";
  }
  if (lower.includes("not running") && lower.includes("ollama")) {
    return "本地 AI 服务还未启动。点击「启动」即可恢复，启动后会自动检查模型。";
  }
  if ((lower.includes("download") || lower.includes("network") || lower.includes("timeout") || lower.includes("connection")) && !lower.includes("manifest")) {
    return "下载没有完成，常见原因是网络不稳定。点击重试会继续下载，不会从头开始。";
  }
  if (lower.includes("disk") || lower.includes("space") || lower.includes("no space")) {
    return "磁盘空间不足。请清理出至少 5 GB 空间后重试安装。";
  }
  const localized = sanitizeLocalModelUserText(text)
    .replace(/Ollama/gi, "本地 AI 应用")
    .replace(/LM Studio/gi, "本机模型服务")
    .replace(/llama\.cpp/gi, "本机模型服务")
    .replace(/OpenAI/gi, "云端服务")
    .replace(/local LLM/gi, "本地 AI")
    .replace(/runtime/gi, "本地 AI 应用")
    .replace(/server/gi, "服务")
    .replace(/backend/gi, "服务");
  if (/^[\x00-\x7F]+$/.test(localized) && /[A-Za-z]/.test(localized)) return fallback;
  return localized;
}

function sanitizeLocalModelUserText(text: string): string {
  return text
    .replace(/[A-Za-z]:\\[^\s，。；;]+/g, "本机路径")
    .replace(/\\\\[^\s，。；;]+/g, "本机路径")
    .replace(/https?:\/\/[^\s，。；;]+/gi, "网络地址")
    .replace(/\b(?:sk|pk|ghp|pat|token|key|secret)[A-Za-z0-9_\-:=.]{6,}\b/gi, "敏感信息")
    .replace(/\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b/gi, "账号信息");
}

function containsSensitiveLocalModelText(text: string): boolean {
  return /[A-Za-z]:\\|\\\\|https?:\/\/|\b(?:token|secret|api[_ -]?key|authorization|bearer)\b/i.test(text);
}
