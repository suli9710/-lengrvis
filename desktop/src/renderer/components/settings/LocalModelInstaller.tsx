import { AlertCircle, CheckCircle2, Download, Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { AppSettings, LocalLLMHealth, LocalModelReadiness, LocalModelSetupPlan } from "../../../shared/types";
import { buildRendererLoopbackBackendWebSocketUrl, type LengrvisApiClient } from "../../lib/apiClient";
import { Badge } from "../Panel";
import { localModelUserMessage } from "./LocalModelSettings";
import { PrivacyReadinessPanel } from "./PrivacyReadinessPanel";

export { PrivacyFlowHint, PrivacyReadinessPanel } from "./PrivacyReadinessPanel";

const LOCAL_MODEL_OPTIONS = [
  { value: "qwen2.5:3b", label: "Qwen2.5 3B" },
  { value: "qwen2.5:7b", label: "Qwen2.5 7B" },
  { value: "llama3.2:3b", label: "Llama 3.2 3B" }
] as const;

const INSTALL_MODEL_WS_PATHS = ["/ws/settings/install-local-model", "/api/ws/settings/install-local-model"] as const;
const INSTALL_MODEL_WS_RETRY_DELAY_MS = 2_500;
const INSTALL_MODEL_WS_MAX_RETRIES = 4;

interface InstallModelProgress {
  stage: string;
  percent: number;
  error?: string;
}

type InstallModelStatus = "idle" | "installing" | "completed" | "error";
type InstallModelSocketStatus = "idle" | "connecting" | "connected" | "reconnecting" | "closed";

export function LocalModelInstaller({
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

