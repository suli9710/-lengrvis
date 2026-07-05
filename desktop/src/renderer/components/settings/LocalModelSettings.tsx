import type { AppSettings } from "../../../shared/settingsTypes";
import type { HardwareAccelerationStatusPayload } from "../../../shared/hardwareAccelerationTypes";
import type { LocalLLMHealth, LocalModelReadiness, LocalModelSetupPlan } from "../../../shared/localModelTypes";

export function LocalLlmHealthNotice({ health }: { health: LocalLLMHealth | null }) {
  const backend = health?.selectedBackend;
  const detail = backend
    ? `已连接本机模型${backend.model ? `：${modelDisplayName(backend.model)}` : ""}`
    : localModelUserMessage(health?.error, "正在检查本地 AI。");
  const probesText = health?.probeOrder.length ? "已完成本机模型和服务检查。" : "正在确认本机模型和服务。";
  const unavailableDetail = health
    ? localLlmUnavailableGuidance(detail)
    : "正在检查本地 AI；完成后会在下方给出下一步。";

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
        <small>{health?.available ? detail : unavailableDetail}</small>
        <small>{probesText}</small>
      </span>
    </div>
  );
}

function localLlmUnavailableGuidance(detail: string): string {
  const lead = detail.replace(/[。！？]+$/, "");
  if (/(还没有可用|还未安装|还未启动|无法检查)/.test(lead)) {
    return `${lead}；请按下方“隐私模式开箱检查”的主按钮继续准备。完成前不会上传文件，也不会静默回退云端。`;
  }
  return `${detail} 隐私模式会等待本地 AI 可用后再继续。`;
}

export function ModelBoundaryProfile({
  mode,
  allowCloudContext,
  allowFileContentUpload,
  localReady,
  localHealth,
  setupPlan,
  hardwareStatus,
  cloudModel
}: {
  mode: AppSettings["mode"];
  allowCloudContext: boolean;
  allowFileContentUpload: boolean;
  localReady: boolean;
  localHealth: LocalLLMHealth | null;
  setupPlan: LocalModelSetupPlan | null;
  hardwareStatus: HardwareAccelerationStatusPayload | null;
  cloudModel: string;
}) {
  const cards = modelBoundaryCards(
    mode,
    allowCloudContext,
    allowFileContentUpload,
    localReady,
    localHealth,
    setupPlan,
    hardwareStatus,
    cloudModel
  );
  return (
    <div className="model-boundary-profile" aria-label="模型边界">
      {cards.map((card) => (
        <div
          key={card.mode}
          className={`model-boundary-profile__item model-boundary-profile__item--${card.tone}${card.mode === mode ? " model-boundary-profile__item--current" : ""}`}
        >
          <div className="model-boundary-profile__item-head">
            <strong>{card.label}</strong>
            {card.mode === mode ? <span>当前</span> : null}
          </div>
          <span>{card.summary}</span>
          <dl className="model-boundary-profile__facts">
            {card.facts.map((fact) => (
              <div key={fact.label}>
                <dt>{fact.label}</dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
          <em>{card.repair}</em>
        </div>
      ))}
    </div>
  );
}

type ModelBoundaryTone = "ready" | "warning" | "blocked";

interface ModelBoundaryFact {
  label: string;
  value: string;
}

interface ModelBoundaryCard {
  mode: AppSettings["mode"];
  label: string;
  summary: string;
  repair: string;
  tone: ModelBoundaryTone;
  facts: ModelBoundaryFact[];
}

function modelBoundaryCards(
  mode: AppSettings["mode"],
  allowCloudContext: boolean,
  allowFileContentUpload: boolean,
  localReady: boolean,
  localHealth: LocalLLMHealth | null,
  setupPlan: LocalModelSetupPlan | null,
  hardwareStatus: HardwareAccelerationStatusPayload | null,
  cloudModel: string
): ModelBoundaryCard[] {
  const recommendedLocalModel = modelDisplayName(
    setupPlan?.model || setupPlan?.readiness?.recommendedModel || localHealth?.readiness?.recommendedModel || localHealth?.selectedBackend?.model || "qwen2.5:3b"
  );
  const cloudModelLabel = cloudModel.trim() || "已配置云端模型";
  const modelSize = localModelSizeEstimate(recommendedLocalModel);
  const hardware = hardwareStatusSummary(hardwareStatus, setupPlan?.readiness ?? localHealth?.readiness);
  const localSpeed = localSpeedEstimate(hardwareStatus, localReady);
  const localRepair = localModelRepairAction(setupPlan, localHealth);

  return [
    {
      mode: "efficiency",
      label: "快速",
      summary: "云端优先，适合长推理、网页和综合规划。",
      repair: "失败修复：检查密钥、服务商、网络或切换到智能混合。",
      tone: mode === "efficiency" ? "warning" : "ready",
      facts: [
        { label: "推荐模型", value: cloudModelLabel },
        { label: "模型大小", value: "不占本机模型盘" },
        { label: "硬件状态", value: "无需本地加速" },
        { label: "速度预估", value: "最快，取决于网络和服务商" }
      ]
    },
    {
      mode: "hybrid",
      label: "智能混合",
      summary: allowCloudContext ? "云端做复杂规划，本机守住私密内容。" : "本机上下文优先，需要时再请求云端。",
      repair: localReady ? "失败修复：本地失败时先给分步修复，再由你决定是否云端继续。" : localRepair,
      tone: localReady ? (allowFileContentUpload ? "warning" : "ready") : "warning",
      facts: [
        { label: "推荐模型", value: `${recommendedLocalModel} + ${cloudModelLabel}` },
        { label: "模型大小", value: modelSize },
        { label: "硬件状态", value: hardware },
        { label: "速度预估", value: localReady ? `规划快；私密任务${localSpeed}` : "本地部分待准备" }
      ]
    },
    {
      mode: "privacy",
      label: "隐私",
      summary: "文件名、摘要、文字识别和向量检索优先留在这台电脑。",
      repair: `${localRepair}；隐私失败不自动回云端。`,
      tone: localReady ? "ready" : "blocked",
      facts: [
        { label: "推荐模型", value: recommendedLocalModel },
        { label: "模型大小", value: modelSize },
        { label: "硬件状态", value: hardware },
        { label: "速度预估", value: localSpeed }
      ]
    }
  ];
}

export function modelDisplayName(model: string): string {
  const normalized = model.trim();
  if (!normalized) return "qwen2.5:3b";
  return normalized;
}

function localModelSizeEstimate(model: string): string {
  const lower = model.toLowerCase();
  if (lower.includes("7b")) return "约 4-6 GB";
  if (lower.includes("3b")) return "约 2-3 GB";
  if (lower.includes("1.5b") || lower.includes("1b")) return "约 1-2 GB";
  return "按模型包显示";
}

function hardwareStatusSummary(
  status: HardwareAccelerationStatusPayload | null,
  readiness?: LocalModelReadiness
): string {
  if (status?.available) {
    const provider = status.selectedProvider || status.configuredProvider || status.executionProvider || status.generationRuntime || "本地 AI";
    return `就绪 · ${provider}`;
  }
  if (status?.error || status?.errors?.length) {
    return "需要处理";
  }
  if (readiness?.gpuSummary) {
    return readiness.gpuSummary;
  }
  if (readiness && !readiness.canInstall) {
    return "低于推荐条件";
  }
  return "待检测，可先走 CPU";
}

function localSpeedEstimate(status: HardwareAccelerationStatusPayload | null, localReady: boolean): string {
  if (!localReady) return "待本地 AI 就绪";
  const provider = `${status?.selectedProvider || status?.configuredProvider || status?.executionProvider || status?.generationRuntime || ""}`.toLowerCase();
  if (status?.available && provider.match(/winml|directml|openvino|gpu|npu/)) return "预计较快，适合摘要和短问答";
  if (status?.available) return "预计中等，适合本地摘要和检索";
  return "CPU 路径较慢，适合短摘要和轻量问答";
}

function localModelRepairAction(setupPlan: LocalModelSetupPlan | null, health: LocalLLMHealth | null): string {
  if (setupPlan?.nextAction === "hardware_blocked") return "失败修复：换高效模式或释放内存/磁盘后重试";
  if (setupPlan?.nextAction === "install_runtime") return "失败修复：下一步安装本地 AI 应用";
  if (setupPlan?.nextAction === "start_runtime") return "失败修复：下一步启动本地 AI 服务";
  if (setupPlan?.nextAction === "use_bundled_model") return "失败修复：下一步启用随包模型";
  if (setupPlan?.nextAction === "download_model") return "失败修复：下一步联网下载推荐模型，模型不包含在主程序安装包内";
  if (setupPlan?.ready || health?.available) return "失败修复：重新检查本地 AI 或切换模型";
  return "失败修复：按下一步准备本地 AI";
}

export function localModelUserMessage(message?: string, fallback = "继续准备本地 AI。"): string {
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
  if (
    (lower.includes("download") || lower.includes("network") || lower.includes("timeout") || lower.includes("connection")) &&
    !lower.includes("manifest")
  ) {
    return "下载没有完成，常见原因是网络不稳定。点击重试会继续下载，不会从头开始。";
  }
  if (lower.includes("disk") || lower.includes("space") || lower.includes("no space")) {
    return "磁盘空间不足。请清理出至少 5 GB 空间后重试安装。";
  }
  if (lower.includes("manifest")) {
    return sanitizeLocalModelUserText(text)
      .replace(/manifest/gi, "资源清单")
      .replace(/bundled/gi, "随包")
      .replace(/runtime/gi, "本地 AI 应用")
      .replace(/ollama/gi, "本地 AI 应用");
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
