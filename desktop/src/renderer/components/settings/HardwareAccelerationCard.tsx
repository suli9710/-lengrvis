import { Download, Loader2 } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import { useCallback, useState } from "react";

import type { AppSettings } from "../../../shared/settingsTypes";
import type {
  HardwareAccelerationRuntime,
  HardwareAccelerationSmokePayload,
  HardwareAccelerationStatusPayload
} from "../../../shared/hardwareAccelerationTypes";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { Badge } from "../Panel";

interface HardwareAccelerationCardProps {
  api: LengrvisApiClient;
  settings: AppSettings;
  status: HardwareAccelerationStatusPayload | null;
  loading: boolean;
  error: string;
  smokeStatus: string;
  smoke: HardwareAccelerationSmokePayload | null;
  runtime: string;
  onRuntimeChange: (runtime: HardwareAccelerationRuntime) => void;
  onSmokeStatusChange: Dispatch<SetStateAction<string>>;
  onSmokeChange: Dispatch<SetStateAction<HardwareAccelerationSmokePayload | null>>;
}

export function HardwareAccelerationCard({
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
    try {
      const response = await api.runHardwareAccelerationSmoke({
        operation,
        prompt: "用中文说一句来自 Lengrvis 硬件加速的问候。",
        maxTokens: 16,
        texts: ["Lengrvis 本地向量模型冒烟测试。"],
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
    } catch (error) { // broad-exception-boundary
      const message = readableHardwareError(error);
      setSmokeError(message);
      onSmokeStatusChange(message);
    } finally {
      setRunningOperation("");
    }
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

function readableHardwareError(error: unknown): string {
  return error instanceof Error && error.message.trim() ? error.message : "硬件冒烟测试失败。";
}


function providerToRuntime(value: string): HardwareAccelerationRuntime {
  const lowered = value.trim().toLowerCase();
  if (!lowered) return "auto";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "winml";
  if (lowered === "directml" || lowered === "dml") return "directml";
  if (lowered === "openvino") return "openvino";
  if (lowered === "cpu") return "cpu";
  return "auto";
}
