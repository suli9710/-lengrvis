import { AlertCircle, CheckCircle2, Download, Loader2, Play, ShieldCheck } from "lucide-react";

import type { AppSettings } from "../../../shared/settingsTypes";
import type { LocalLLMHealth, LocalModelSetupPlan } from "../../../shared/localModelTypes";
import { Badge } from "../Panel";
import { localModelUserMessage, modelDisplayName } from "./LocalModelSettings";

function displayMode(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "快速";
  if (mode === "hybrid") return "智能混合";
  return "隐私";
}

export function PrivacyReadinessPanel({
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

export function PrivacyFlowHint() {
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
