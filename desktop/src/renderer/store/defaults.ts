import type { AppSettings, BackendStatus, Plan, SafetyReview, SystemInfo } from "../../shared/types";

export const defaultApiBaseUrl = "http://127.0.0.1:8000";

export const defaultSettings: AppSettings = {
  apiBaseUrl: defaultApiBaseUrl,
  autoStartBackend: false,
  telemetryEnabled: false,
  compactMode: false,
  theme: "system",
  providerName: "openai_compatible",
  model: "gpt-4o-mini",
  reviewModel: "",
  wireApi: "chat_completions",
  requiresOpenAiAuth: true,
  modelReasoningEffort: "medium",
  disableResponseStorage: false,
  temperature: 0.2,
  maxTokens: 1600,
  timeout: 30,
  llmApiMaxRetries: 2,
  llmApiRetryBackoffSeconds: 0.25,
  llmApiCircuitFailureThreshold: 5,
  llmApiCircuitCooldownSeconds: 30,
  modelContextWindow: 128000,
  modelAutoCompactTokenLimit: 96000,
  workspaceRoot: "",
  allowedDirectories: [],
  allowBrowserNetwork: false,
  remoteDesktopEnabled: false,
  appAllowlist: [],
  browserMaxPageBytes: 250000,
  browserScreenshotDir: "",
  onnxModelPath: "",
  onnxExecutionProvider: "",
  onnxProviderPreference: "winml,directml,openvino,cpu",
  onnxDirectmlDeviceId: "",
  onnxOpenvinoDevice: "AUTO",
  onnxOpenvinoCacheDir: "",
  onnxWarmOnStartup: false,
  onnxModelFamily: "",
  onnxEmbeddingBackend: "auto",
  onnxEmbeddingModelPath: "",
  onnxEmbeddingExecutionProvider: "",
  onnxEmbeddingModelId: "intfloat/multilingual-e5-small",
  onnxEmbeddingMaxBatchSize: 32,
  imageEmbeddingBackend: "auto",
  onnxImageEmbeddingModelPath: "",
  onnxImageEmbeddingExecutionProvider: "",
  onnxImageEmbeddingModelId: "openai/clip-vit-base-patch32",
  onnxImageEmbeddingMaxBatchSize: 8,
  ocrBackend: "auto",
  ocrExecutionProvider: "",
  ocrOpenvinoModelDir: "",
  ocrOpenvinoDevice: "AUTO",
  ocrLang: "multi",
  ocrMinConfidence: 0,
  ocrBatchSize: 1,
  mode: "efficiency",
  permissionMode: "default",
  allowCloudContext: false,
  allowFileContentUpload: false,
  mcpServers: []
};

export const disconnectedStatus: BackendStatus = {
  state: "not_configured",
  baseUrl: defaultApiBaseUrl,
  message: "等待后端连接",
  lastCheckedAt: new Date().toISOString(),
  health: {
    ok: false
  }
};

export const emptyPlan: Plan = {
  id: "plan-empty",
  title: "暂无执行计划",
  objective: "发送任务后，Lengrvis 会在这里显示真实执行计划。",
  updatedAt: new Date().toISOString(),
  steps: []
};

export const emptySafetyReview: SafetyReview = {
  id: "safety-empty",
  status: "clear",
  updatedAt: new Date().toISOString(),
  findings: []
};

export const emptySystemInfo: SystemInfo = {
  appVersion: window.lengrvis?.versions.app ?? "0.1.1",
  electronVersion: window.lengrvis?.versions.electron ?? "未知",
  chromeVersion: window.lengrvis?.versions.chrome ?? "未知",
  nodeVersion: window.lengrvis?.versions.node ?? "未知",
  platform: window.lengrvis?.platform ?? "win32",
  arch: "",
  backendBaseUrl: defaultApiBaseUrl,
  diagnostics: {
    info: {},
    disks: [],
    network: {},
    topProcesses: [],
    startupItems: [],
    suggestions: []
  },
  processes: [],
  startupItems: [],
  installedApps: []
};
