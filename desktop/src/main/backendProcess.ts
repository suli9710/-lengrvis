import { app } from "electron";
import { execFile, spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { appendFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { cwd as getCwd } from "node:process";

import type { BackendStatus } from "../shared/types";
import { assertLoopbackBackendUrl } from "./backendUrl";
import { resolveDesktopApiToken } from "./desktopApiToken";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const HEALTH_ENDPOINT = "/health";
const RUNTIME_STATUS_ENDPOINT = "/api/runtime/status";
const RUNTIME_FOREGROUND_ENDPOINT = "/api/runtime/foreground";
const RUNTIME_BACKGROUND_ENDPOINT = "/api/runtime/background";
const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";
const REDACTED_LOG_VALUE = "[redacted]";
const SENSITIVE_LOG_KEY_PATTERN =
  "x-lengrvis-desktop-token|authorization|cookie|set-cookie|api[_-]?key|apikey|desktop[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|oauth[_-]?token|client[_-]?secret|token|secret|password|passwd|pwd|jwt|session(?:[_-]?id)?|otp|passcode|one[_-]?time[_-]?code|verification[_-]?code";
const SENSITIVE_URL_PARAM_PATTERN =
  "access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|oauth[_-]?token|desktop[_-]?token|token|api[_-]?key|apikey|key|client[_-]?secret|secret|password|passwd|pwd|authorization|auth|cookie|session(?:[_-]?id)?|jwt|code|otp|passcode|one[_-]?time[_-]?code|verification[_-]?code";
const URL_IN_TEXT_REGEX = /https?:\/\/[^\s'"<>]+/gi;
const SENSITIVE_URL_PARAM_REGEX = new RegExp(`([?&#](?:${SENSITIVE_URL_PARAM_PATTERN})=)[^&#\\s]+`, "gi");
const CLI_SECRET_ARG_REGEX = new RegExp(
  `(--?(?:${SENSITIVE_LOG_KEY_PATTERN})\\b(?:=|\\s+|["']?\\s*,\\s*["']?))[^"',\\s\\]]+`,
  "gi"
);
const KEY_VALUE_SECRET_REGEX = new RegExp(
  `(["']?\\b(?:${SENSITIVE_LOG_KEY_PATTERN})\\b["']?\\s*[:=]\\s*["']?)(?:Bearer\\s+)?[^"',;\\s}&]+`,
  "gi"
);
const AUTHORIZATION_HEADER_REGEX = /\b(Authorization)\s*:\s*[^\r\n]+/gi;
const COOKIE_HEADER_REGEX = /\b(Set-Cookie|Cookie)\s*:\s*[^\r\n]+/gi;
const BEARER_TOKEN_REGEX = /\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b/gi;
const OPENAI_KEY_REGEX = /\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b/g;
const PRIVATE_KEY_BLOCK_REGEX = /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;
const DEFAULT_WINDOWS_SERVICE_NAMES = [
  "LengrvisBackend",
  "Lengrvis Backend",
  "LengrvisService",
  "Lengrvis Service",
  "Lengrvis"
] as const;

function envAliases(name: string): string[] {
  return [name];
}

function env(name: string): string | undefined {
  for (const alias of envAliases(name)) {
    const value = process.env[alias];
    if (value) return value;
  }
  return undefined;
}

function envList(name: string): string[] {
  return envAliases(name).flatMap((alias) => splitList(process.env[alias]));
}

function envWithAliases(name: string, value: string): NodeJS.ProcessEnv {
  return Object.fromEntries(envAliases(name).map((alias) => [alias, process.env[alias] ?? value]));
}

export interface BackendProcessOptions {
  baseUrl?: string;
  command?: string;
  args?: string[];
  cwd?: string;
  windowsServiceNames?: string[];
}

interface WindowsServiceProbe {
  checked: boolean;
  exists: boolean;
  running: boolean;
  serviceName?: string;
  stateCode?: number;
  stateName?: string;
  error?: string;
}

interface WindowsServiceQueryResult {
  exists: boolean;
  running: boolean;
  serviceName: string;
  stateCode?: number;
  stateName?: string;
  error?: string;
}

export class BackendProcessManager {
  private child: ChildProcessWithoutNullStreams | null = null;
  private status: BackendStatus;
  private managedWindowsServiceName: string | null = null;
  private readonly desktopApiToken: string;
  private readonly backendConfigDir: string;
  private readonly backendDataDir: string;

  constructor(private readonly options: BackendProcessOptions = {}) {
    const command = this.resolveBackendCommand();
    const tokenResolution = resolveDesktopApiToken({ command });
    this.desktopApiToken = tokenResolution.token;
    this.backendConfigDir = tokenResolution.configDir;
    this.backendDataDir = tokenResolution.dataDir;
    for (const alias of envAliases("LENGRVIS_DESKTOP_API_TOKEN")) {
      process.env[alias] = process.env[alias] ?? this.desktopApiToken;
    }
    for (const alias of envAliases("LENGRVIS_CONFIG_DIR")) {
      process.env[alias] = process.env[alias] ?? this.backendConfigDir;
    }
    for (const alias of envAliases("LENGRVIS_DATA_DIR")) {
      process.env[alias] = process.env[alias] ?? this.backendDataDir;
    }
    this.status = {
      state: "stopped",
      baseUrl: this.getBaseUrl(),
      lastCheckedAt: new Date().toISOString()
    };
  }

  getBaseUrl(): string {
    return this.options.baseUrl ?? env("LENGRVIS_BACKEND_URL") ?? DEFAULT_BACKEND_URL;
  }

  getDesktopApiToken(): string {
    return this.desktopApiToken;
  }

  async start(): Promise<BackendStatus> {
    if (this.child && !this.child.killed) {
      return this.refreshStatus("running", "后端进程已在运行");
    }

    const service = await this.detectRunningWindowsService();
    if (service.running) {
      return this.connectToWindowsService(service);
    }

    if (this.isServiceManaged(service)) {
      return this.waitForWindowsService(service);
    }

    if (service.checked) {
      await writeBackendLog(
        service.error
          ? `windows service probe failed; fallback=child-process; error=${service.error}`
          : "windows service not running; fallback=child-process"
      );
    }

    const command = this.resolveBackendCommand();
    const args = this.options.args ?? splitArgs(env("LENGRVIS_BACKEND_ARGS"));
    await writeBackendLog(`start requested; command=${command ?? "<none>"} args=${JSON.stringify(args)} resourcesPath=${process.resourcesPath} appPath=${app.getAppPath()} isPackaged=${app.isPackaged} defaultApp=${String(process.defaultApp)}`);

    if (!command) {
      return this.refreshStatus(
        "not_configured",
        "未配置后端命令，将使用外部后端地址"
      );
    }

    this.status = this.makeStatus("starting", "正在启动后端进程");

    try {
      const bundledOllamaEnv = resolveBundledOllamaEnv(command);
      this.child = spawn(command, args, {
        cwd: this.options.cwd ?? env("LENGRVIS_BACKEND_CWD") ?? dirname(command),
        env: {
          ...process.env,
          ...envWithAliases("LENGRVIS_CONFIG_DIR", this.backendConfigDir),
          ...envWithAliases("LENGRVIS_DATA_DIR", this.backendDataDir),
          ...envWithAliases("LENGRVIS_FULL_BACKEND", "1"),
          ...(app.isPackaged ? envWithAliases("LENGRVIS_COMMERCIAL_RELEASE", "true") : {}),
          ...envWithAliases("LENGRVIS_BACKEND_URL", this.getBaseUrl()),
          ...envWithAliases("LENGRVIS_DESKTOP_API_TOKEN", this.desktopApiToken),
          ...bundledOllamaEnv
        },
        windowsHide: true
      });

      this.child.stdout.on("data", (chunk) => {
        void writeBackendLog(`[stdout] ${chunk.toString().trimEnd()}`);
      });

      this.child.stderr.on("data", (chunk) => {
        void writeBackendLog(`[stderr] ${chunk.toString().trimEnd()}`);
      });

      this.child.once("exit", (code) => {
        void writeBackendLog(`backend process exited; code=${code}`);
        this.child = null;
        this.status = this.makeStatus(
          code === 0 ? "stopped" : "error",
          code === 0 ? "后端进程已停止" : `后端进程异常退出，代码 ${code}`
        );
      });

      this.child.once("error", (error) => {
        void writeBackendLog(`backend process error; message=${error.message}`);
        this.child = null;
        this.status = this.makeStatus("error", error.message);
      });

      return this.refreshStatus("starting", "Backend process started; waiting for health check");
    } catch (error) {
      const message = error instanceof Error ? error.message : "无法启动后端进程";
      this.status = this.makeStatus("error", message);
      return this.status;
    }
  }

  async stop(): Promise<BackendStatus> {
    if (!this.child || this.child.killed) {
      this.child = null;
      const message = this.status.message?.includes("Windows Service")
        ? "Windows Service 由系统托管，未停止"
        : "后端进程未运行";
      return this.refreshStatus("stopped", message);
    }

    const child = this.child;
    this.child = null;
    // Graceful drain before the hard kill: ask the backend to pause in-flight
    // runs (bounded wait) so they survive as resumable PAUSED rows instead of
    // crash-orphaned RUNNING zombies that startup recovery has to reconcile.
    const drainError = await postRuntimeMode(
      this.getBaseUrl(),
      RUNTIME_BACKGROUND_ENDPOINT,
      "desktop_quit",
      this.desktopApiToken,
      10_000
    );
    if (drainError) {
      void writeBackendLog(`backend drain before stop failed; error=${drainError.message}`);
    }
    await terminateProcessTree(child);
    return this.refreshStatus("stopped", "后端进程已停止");
  }

  async getStatus(): Promise<BackendStatus> {
    if (this.managedWindowsServiceName) {
      const service = await this.detectRunningWindowsService([this.managedWindowsServiceName]);
      if (service.running) {
        return this.connectToWindowsService(service);
      }
      if (this.isServiceManaged(service)) {
        return this.waitForWindowsService(service);
      }

      await writeBackendLog(
        `windows service no longer running; service=${this.managedWindowsServiceName}; fallback=child-process`
      );
      this.managedWindowsServiceName = null;
      return this.start();
    }

    const processState = this.child && !this.child.killed ? "starting" : this.status.state;
    return this.refreshStatus(processState, this.status.message);
  }

  async enterForeground(reason = "desktop_opened"): Promise<BackendStatus> {
    await this.start();
    const runtimeModeError = await postRuntimeMode(this.getBaseUrl(), RUNTIME_FOREGROUND_ENDPOINT, reason, this.desktopApiToken);
    const status = await this.getStatus();
    return runtimeModeError ? this.withRuntimeModeError(status, "foreground", runtimeModeError) : status;
  }

  async enterBackground(reason = "tray_background"): Promise<BackendStatus> {
    await this.start();
    const runtimeModeError = await postRuntimeMode(this.getBaseUrl(), RUNTIME_BACKGROUND_ENDPOINT, reason, this.desktopApiToken);
    const status = await this.getStatus();
    return runtimeModeError ? this.withRuntimeModeError(status, "background", runtimeModeError) : status;
  }

  private async connectToWindowsService(service: WindowsServiceProbe): Promise<BackendStatus> {
    const serviceLabel = service.serviceName ? `Windows Service：${service.serviceName}` : "Windows Service";
    this.managedWindowsServiceName = service.serviceName ?? this.managedWindowsServiceName;
    await writeBackendLog(`windows service running; service=${service.serviceName ?? "<unknown>"}; probing ${this.getBaseUrl()}`);
    const health = await probeHealth(this.getBaseUrl());
    this.status = health.ok
      ? this.makeStatus("running", `已连接到 ${serviceLabel}`, health)
      : this.makeStatus("error", `${serviceLabel} 正在运行，但健康检查失败`, health);
    return this.status;
  }

  private async waitForWindowsService(service: WindowsServiceProbe): Promise<BackendStatus> {
    const serviceLabel = service.serviceName ? `Windows Service：${service.serviceName}` : "Windows Service";
    this.managedWindowsServiceName = service.serviceName ?? this.managedWindowsServiceName;
    await writeBackendLog(
      `windows service managed but not running; service=${service.serviceName ?? "<unknown>"} state=${service.stateName ?? "<unknown>"}`
    );
    const health = await probeHealth(this.getBaseUrl());
    this.status = health.ok
      ? this.makeStatus("running", `已连接到 ${serviceLabel}`, health)
      : this.makeStatus("starting", `${serviceLabel} ${formatServiceState(service)}，等待服务就绪`, health);
    return this.status;
  }

  private isServiceManaged(service: WindowsServiceProbe): boolean {
    return Boolean(service.exists && service.stateCode !== 1);
  }

  private async refreshStatus(
    fallbackState: BackendStatus["state"],
    fallbackMessage?: string
  ): Promise<BackendStatus> {
    const health = await probeHealth(this.getBaseUrl());
    const runtime = health.ok ? await probeRuntimeStatus(this.getBaseUrl()) : {};
    const hasConfiguredCommand = Boolean(this.resolveBackendCommand());

    if (health.ok) {
      this.status = this.makeStatus(
        "running",
        fallbackState === "running" || fallbackMessage?.includes("Windows Service")
          ? fallbackMessage ?? "后端已连接"
          : "后端已连接",
        health,
        runtime
      );
    } else if (!hasConfiguredCommand && fallbackState !== "error") {
      this.status = this.makeStatus(
        "not_configured",
        fallbackMessage ?? "等待外部后端",
        health,
        runtime
      );
    } else {
      const state = fallbackState === "running" ? "starting" : fallbackState;
      const message = fallbackState === "running" && !fallbackMessage
        ? "Backend process is running; waiting for health check"
        : fallbackMessage;
      this.status = this.makeStatus(state, message, health, runtime);
    }

    return this.status;
  }

  private makeStatus(
    state: BackendStatus["state"],
    message?: string,
    health?: BackendStatus["health"],
    runtime?: Partial<BackendStatus>
  ): BackendStatus {
    return {
      state,
      baseUrl: this.getBaseUrl(),
      pid: this.child?.pid,
      message,
      health,
      ...runtime,
      lastCheckedAt: new Date().toISOString()
    };
  }

  private withRuntimeModeError(status: BackendStatus, mode: "foreground" | "background", error: Error): BackendStatus {
    const message = redactBackendLogText(error.message);
    return {
      ...status,
      message: `Backend stayed available, but could not enter ${mode} runtime mode: ${message}`,
      runtimeModeError: message,
      lastCheckedAt: new Date().toISOString()
    };
  }

  private resolveBackendCommand(): string | undefined {
    if (this.options.command) {
      return this.options.command;
    }

    const configuredCommand = env("LENGRVIS_BACKEND_COMMAND");
    if (configuredCommand) {
      return configuredCommand;
    }

    const packagedBackend = join(process.resourcesPath, "backend", process.platform === "win32" ? "backend.exe" : "backend");
    if (existsSync(packagedBackend)) {
      return packagedBackend;
    }

    const developmentBackend = join(getCwd(), "dist", process.platform === "win32" ? "backend.exe" : "backend");
    if (existsSync(developmentBackend)) {
      return developmentBackend;
    }

    return undefined;
  }

  private async detectRunningWindowsService(serviceNames = this.getWindowsServiceNames()): Promise<WindowsServiceProbe> {
    if (process.platform !== "win32" || env("LENGRVIS_BACKEND_SERVICE_DISABLED") === "1") {
      return { checked: false, exists: false, running: false };
    }

    let firstExistingService: WindowsServiceQueryResult | null = null;
    let firstError: string | undefined;

    for (const serviceName of serviceNames) {
      const result = await queryWindowsService(serviceName);
      if (result.running) {
        return {
          checked: true,
          exists: result.exists,
          running: true,
          serviceName: result.serviceName,
          stateCode: result.stateCode,
          stateName: result.stateName
        };
      }
      if (result.exists && !firstExistingService) {
        firstExistingService = result;
      }
      firstError ??= result.error;
    }

    return {
      checked: true,
      exists: Boolean(firstExistingService),
      running: false,
      serviceName: firstExistingService?.serviceName,
      stateCode: firstExistingService?.stateCode,
      stateName: firstExistingService?.stateName,
      error: firstError
    };
  }

  private getWindowsServiceNames(): string[] {
    const configuredNames = [
      ...envList("LENGRVIS_BACKEND_SERVICE_NAME"),
      ...envList("LENGRVIS_SERVICE_NAME")
    ];

    return [...new Set([...configuredNames, ...this.options.windowsServiceNames ?? [], ...DEFAULT_WINDOWS_SERVICE_NAMES])];
  }
}

function formatServiceState(service: WindowsServiceProbe): string {
  return service.stateName ? `处于 ${service.stateName}` : "未就绪";
}

const PROCESS_EXIT_TIMEOUT_MS = 5000;

/**
 * Terminate the backend process and all of its descendants.
 *
 * On Windows, `child.kill()` maps to TerminateProcess which does NOT kill the
 * process tree: the PyInstaller onefile bootstrap spawns the real backend as a
 * child that would survive as an orphan, keeping port 8000 and GPU memory.
 * `taskkill /T /F` kills the whole tree. On other platforms SIGTERM followed
 * by SIGKILL after a timeout is used.
 */
export async function terminateProcessTree(child: ChildProcessWithoutNullStreams): Promise<void> {
  const pid = child.pid;
  if (!pid) {
    child.kill();
    return;
  }

  const exited = waitForExit(child, PROCESS_EXIT_TIMEOUT_MS);

  if (process.platform === "win32") {
    await new Promise<void>((resolve) => {
      execFile(
        "taskkill",
        ["/PID", String(pid), "/T", "/F"],
        { timeout: PROCESS_EXIT_TIMEOUT_MS, windowsHide: true },
        (error) => {
          if (error) {
            void writeBackendLog(`taskkill failed for pid=${pid}; falling back to kill(); error=${error.message}`);
            child.kill();
          }
          resolve();
        }
      );
    });
  } else {
    child.kill("SIGTERM");
  }

  const finished = await exited;
  if (!finished) {
    void writeBackendLog(`backend process pid=${pid} did not exit within ${PROCESS_EXIT_TIMEOUT_MS}ms; sending SIGKILL`);
    try {
      child.kill("SIGKILL");
    } catch {
      // Process may already be gone.
    }
  }
}

function waitForExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    if (child.exitCode !== null || child.killed) {
      resolve(true);
      return;
    }
    const timer = setTimeout(() => resolve(false), timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

function resolveBundledOllamaEnv(command: string): NodeJS.ProcessEnv {
  const resourcesDir = resolveResourcesDir(command);
  const ollamaDir = join(resourcesDir, "ollama");
  const modelsDir = join(resourcesDir, "ollama-models");
  const manifestPath = join(resourcesDir, "ollama-bundle-manifest.json");
  const env: NodeJS.ProcessEnv = {};

  if (existsSync(ollamaDir)) {
    Object.assign(env, envWithAliases("LENGRVIS_BUNDLED_OLLAMA_DIR", ollamaDir));
  }

  if (existsSync(modelsDir)) {
    Object.assign(env, envWithAliases("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", modelsDir));
    env.OLLAMA_MODELS = process.env.OLLAMA_MODELS ?? modelsDir;
  }

  if (existsSync(manifestPath)) {
    Object.assign(env, envWithAliases("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", manifestPath));
  }

  return env;
}

function resolveResourcesDir(command: string): string {
  const packagedResourcesDir = process.resourcesPath;
  if (existsSync(join(packagedResourcesDir, "backend"))) {
    return packagedResourcesDir;
  }

  const commandResourcesDir = join(dirname(command), "..");
  if (existsSync(join(commandResourcesDir, "backend"))) {
    return commandResourcesDir;
  }

  return packagedResourcesDir;
}

export function redactBackendLogText(message: string): string {
  return message
    .replace(PRIVATE_KEY_BLOCK_REGEX, "[redacted:private-key]")
    .replace(URL_IN_TEXT_REGEX, (match) => redactLogUrl(match))
    .replace(AUTHORIZATION_HEADER_REGEX, `$1: ${REDACTED_LOG_VALUE}`)
    .replace(COOKIE_HEADER_REGEX, `$1: ${REDACTED_LOG_VALUE}`)
    .replace(CLI_SECRET_ARG_REGEX, `$1${REDACTED_LOG_VALUE}`)
    .replace(KEY_VALUE_SECRET_REGEX, `$1${REDACTED_LOG_VALUE}`)
    .replace(BEARER_TOKEN_REGEX, `Bearer ${REDACTED_LOG_VALUE}`)
    .replace(OPENAI_KEY_REGEX, `sk-${REDACTED_LOG_VALUE}`);
}

function redactLogUrl(value: string): string {
  try {
    const parsed = new URL(value);
    if (parsed.username) {
      parsed.username = REDACTED_LOG_VALUE;
    }
    if (parsed.password) {
      parsed.password = REDACTED_LOG_VALUE;
    }
    for (const key of [...parsed.searchParams.keys()]) {
      if (isSensitiveUrlParam(key)) {
        parsed.searchParams.set(key, REDACTED_LOG_VALUE);
      }
    }
    parsed.hash = redactLogUrlFragment(parsed.hash);
    return parsed.toString();
  } catch {
    return redactLogUrlFragment(value.replace(SENSITIVE_URL_PARAM_REGEX, `$1${REDACTED_LOG_VALUE}`));
  }
}

function redactLogUrlFragment(hash: string): string {
  if (!hash) {
    return hash;
  }
  return hash.replace(SENSITIVE_URL_PARAM_REGEX, `$1${REDACTED_LOG_VALUE}`);
}

function isSensitiveUrlParam(key: string): boolean {
  const normalized = key.toLowerCase().replace(/[-.]/g, "_");
  return /(?:^|_)(?:token|secret|password|passwd|pwd|authorization|auth|cookie|session|jwt|otp|passcode|code|key)(?:_|$)/.test(normalized)
    || normalized === "apikey"
    || normalized.endsWith("_api_key")
    || normalized.endsWith("_client_secret");
}

export async function writeBackendLog(message: string): Promise<void> {
  try {
    const logDir = app.getPath("userData");
    await mkdir(logDir, { recursive: true });
    await appendFile(join(logDir, "backend-process.log"), `[${new Date().toISOString()}] ${redactBackendLogText(message)}\n`, "utf8");
  } catch {
    // Logging must never block app startup.
  }
}

function splitArgs(value?: string): string[] {
  if (!value) {
    return [];
  }

  return value.match(/(?:[^\s"]+|"[^"]*")+/g)?.map((item) => item.replace(/^"|"$/g, "")) ?? [];
}

function splitList(value?: string): string[] {
  return value
    ?.split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean) ?? [];
}

function queryWindowsService(serviceName: string): Promise<WindowsServiceQueryResult> {
  return new Promise((resolve) => {
    execFile(
      "sc.exe",
      ["query", serviceName],
      { timeout: 1500, windowsHide: true },
      (error, stdout, stderr) => {
        const output = `${stdout}\n${stderr}`;
        const serviceNotFound = /does not exist|FAILED 1060/i.test(output);
        const exists = !serviceNotFound && (output.includes("SERVICE_NAME") || output.includes("STATE"));
        const stateMatch = output.match(/STATE\s*:\s*(\d+)\s+([A-Z_]+)/i);
        const stateCode = stateMatch ? Number.parseInt(stateMatch[1], 10) : undefined;
        const stateName = stateMatch?.[2];
        const running = exists && stateCode === 4;

        resolve({
          exists,
          running,
          serviceName,
          stateCode,
          stateName,
          error: error && !serviceNotFound && !exists ? error.message : undefined
        });
      }
    );
  });
}

async function probeHealth(baseUrl: string): Promise<NonNullable<BackendStatus["health"]>> {
  const startedAt = Date.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1500);

  try {
    const response = await fetch(new URL(HEALTH_ENDPOINT, baseUrl), {
      method: "GET",
      signal: controller.signal
    });

    const data = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
    const mode = typeof data.mode === "string" ? data.mode : "";
    const shellMode = typeof data.shellMode === "string" ? data.shellMode : "";
    const fullBackendState = typeof data.fullBackendState === "string" ? data.fullBackendState : "";
    const guardianReady = mode === "guardian" && shellMode === "foreground" && fullBackendState === "running";
    const ok = response.ok && (mode !== "guardian" || guardianReady);
    return {
      ok,
      latencyMs: Date.now() - startedAt
    };
  } catch {
    return {
      ok: false,
      latencyMs: Date.now() - startedAt
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function probeRuntimeStatus(baseUrl: string): Promise<Partial<BackendStatus>> {
  try {
    const response = await fetch(new URL(RUNTIME_STATUS_ENDPOINT, baseUrl), {
      method: "GET",
      signal: AbortSignal.timeout(1500)
    });
    if (!response.ok) {
      return {};
    }
    const data = await response.json() as Record<string, unknown>;
    return {
      shellMode: data.shellMode === "foreground" ? "foreground" : data.shellMode === "background" ? "background" : undefined,
      guardianState: stringValue(data.guardianState),
      fullBackendState: stringValue(data.fullBackendState),
      fullBackendPort: typeof data.fullBackendPort === "number" ? data.fullBackendPort : undefined,
      lastWakeReason: stringValue(data.lastWakeReason)
    };
  } catch {
    return {};
  }
}

async function postRuntimeMode(
  baseUrl: string,
  endpoint: string,
  reason: string,
  desktopApiToken: string,
  timeoutMs = 35_000
): Promise<Error | null> {
  try {
    const backendUrl = assertLoopbackBackendUrl(baseUrl, "Runtime mode desktop token request");
    const response = await fetch(new URL(endpoint, backendUrl), {
      method: "POST",
      headers: { "Content-Type": "application/json", [DESKTOP_API_TOKEN_HEADER]: desktopApiToken },
      body: JSON.stringify({ reason }),
      signal: AbortSignal.timeout(timeoutMs)
    });
    if (!response.ok) {
      throw new Error(`Runtime mode request failed: ${response.status} ${await runtimeModeErrorText(response)}`);
    }
    return null;
  } catch (error) {
    return error instanceof Error ? error : new Error("Runtime mode request failed");
  }
}

async function runtimeModeErrorText(response: Response): Promise<string> {
  try {
    const data = await response.clone().json() as Record<string, unknown>;
    const detail = stringValue(data.detail) ?? stringValue(data.message) ?? stringValue(data.error);
    return detail ? `(${detail})` : response.statusText;
  } catch {
    const text = await response.text().catch(() => "");
    return text.trim() || response.statusText;
  }
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
