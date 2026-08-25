import { app } from "electron";
import { execFile, spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { sign as signEd25519, type KeyObject } from "node:crypto";
import { existsSync } from "node:fs";
import { appendFile, mkdir, rename, rm, stat, truncate } from "node:fs/promises";
import { dirname, join } from "node:path";
import { cwd as getCwd } from "node:process";

import type { BackendStatus } from "../shared/types";
import { ApprovalSessionGenerationManager } from "./approvalSessionGeneration";
import { BackendControlTransport } from "./backendControlTransport";
import { BackendLifecycleCoordinator } from "./backendLifecycle";
import {
  forcedEnv,
  hardenPackagedProcessEnvironment,
  packagedBackendConfigDir,
  packagedBackendEnvironment,
  setResolvedProcessEnv
} from "./backendProcessEnvironment";
import { resolveDesktopApiToken } from "./desktopApiToken";
import { resolveNativeConfirmationKey } from "./nativeConfirmationKey";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const DEFAULT_ACTIVATION_BASE_URL = "https://agent.lengzhehao.com";
const DEFAULT_LICENSE_PUBLIC_KEY = "ed25519:0LY7FXJpX494464DDN_vqSbqgCMX4sAj2iwf5gmC5c4";
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
export const BACKEND_LOG_MAX_BYTES = 512 * 1024;
export const BACKEND_LOG_MAX_ENTRY_BYTES = 64 * 1024;
const BACKEND_LOG_ROTATED_SUFFIX = ".1";
const BACKEND_LOG_TRUNCATION_MARKER = " [truncated]";
let backendLogWriteQueue: Promise<void> = Promise.resolve();

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
  configDir?: string;
  dataDir?: string;
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
  private readonly controlTransport: BackendControlTransport;
  private readonly approvalSessionGeneration: ApprovalSessionGenerationManager;
  private readonly nativeConfirmationPrivateKey: KeyObject;
  private readonly nativeConfirmationPublicKey: string;
  private readonly backendConfigDir: string;
  private readonly backendDataDir: string;
  private readonly lifecycle: BackendLifecycleCoordinator<BackendStatus>;

  constructor(private readonly options: BackendProcessOptions = {}) {
    if (app.isPackaged) {
      hardenPackagedProcessEnvironment(DEFAULT_BACKEND_URL);
    }
    const command = this.resolveBackendCommand();
    const tokenResolution = resolveDesktopApiToken({
      command,
      configDir: this.options.configDir ?? (
        app.isPackaged ? packagedBackendConfigDir(command, process.resourcesPath) : undefined
      ),
      dataDir: this.options.dataDir ?? (app.isPackaged ? app.getPath("userData") : undefined)
    });
    this.desktopApiToken = tokenResolution.token;
    this.controlTransport = new BackendControlTransport(() => this.getBaseUrl(), this.desktopApiToken);
    this.backendConfigDir = tokenResolution.configDir;
    this.backendDataDir = tokenResolution.dataDir;
    this.approvalSessionGeneration = new ApprovalSessionGenerationManager({ dataDir: this.backendDataDir });
    const nativeConfirmationKey = resolveNativeConfirmationKey({ dataDir: this.backendDataDir });
    this.nativeConfirmationPrivateKey = nativeConfirmationKey.privateKey;
    this.nativeConfirmationPublicKey = nativeConfirmationKey.publicKey;
    setResolvedProcessEnv("LENGRVIS_DESKTOP_API_TOKEN", this.desktopApiToken, app.isPackaged);
    setResolvedProcessEnv("LENGRVIS_CONFIG_DIR", this.backendConfigDir, app.isPackaged);
    setResolvedProcessEnv("LENGRVIS_DATA_DIR", this.backendDataDir, app.isPackaged);
    this.status = {
      state: "stopped",
      baseUrl: this.getBaseUrl(),
      lastCheckedAt: new Date().toISOString()
    };
    this.lifecycle = new BackendLifecycleCoordinator(
      () => this.startOperation(),
      () => this.stopOperation()
    );
  }

  getBaseUrl(): string {
    if (this.options.baseUrl) {
      return this.options.baseUrl;
    }
    return app.isPackaged ? DEFAULT_BACKEND_URL : env("LENGRVIS_BACKEND_URL") ?? DEFAULT_BACKEND_URL;
  }

  getDesktopApiToken(): string {
    return this.controlTransport.getVerifiedDesktopApiToken();
  }

  getNativeConfirmationPublicKey(): string {
    return this.nativeConfirmationPublicKey;
  }

  signNativeConfirmationPayload(payload: string): string {
    const sessionBoundPayload = this.approvalSessionGeneration.bindSigningPayload(payload);
    return signEd25519(null, Buffer.from(sessionBoundPayload, "utf-8"), this.nativeConfirmationPrivateKey)
      .toString("base64url");
  }

  initializeApprovalSessionGeneration(): void {
    this.approvalSessionGeneration.initialize();
  }

  rotateApprovalSessionGeneration(): void {
    this.approvalSessionGeneration.rotate();
  }

  activateApprovalSessionGeneration(): void {
    this.approvalSessionGeneration.activate();
  }

  deactivateApprovalSessionGeneration(): void {
    this.approvalSessionGeneration.deactivate();
  }

  start(): Promise<BackendStatus> {
    return this.lifecycle.start();
  }

  private async startOperation(): Promise<BackendStatus> {
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
    const args = this.options.args ?? (app.isPackaged ? [] : splitArgs(env("LENGRVIS_BACKEND_ARGS")));
    await writeBackendLog(`start requested; command=${command ?? "<none>"} args=${JSON.stringify(args)} resourcesPath=${process.resourcesPath} appPath=${app.getAppPath()} isPackaged=${app.isPackaged} defaultApp=${String(process.defaultApp)}`);

    if (!command) {
      return this.refreshStatus(
        "not_configured",
        "未配置后端命令，将使用外部后端地址"
      );
    }

    this.status = this.makeStatus("starting", "正在启动后端进程");
    this.controlTransport.invalidateIdentity();

    try {
      const bundledOllamaEnv = resolveBundledOllamaEnv(command);
      const child = spawn(command, args, {
        cwd: this.options.cwd ?? (app.isPackaged ? dirname(command) : env("LENGRVIS_BACKEND_CWD") ?? dirname(command)),
        env: {
          ...process.env,
          ...forcedEnv("LENGRVIS_CONFIG_DIR", this.backendConfigDir),
          ...forcedEnv("LENGRVIS_DATA_DIR", this.backendDataDir),
          ...forcedEnv("LENGRVIS_FULL_BACKEND", "1"),
          ...(app.isPackaged ? packagedBackendEnvironment({
            activationBaseUrl: DEFAULT_ACTIVATION_BASE_URL,
            licensePublicKey: DEFAULT_LICENSE_PUBLIC_KEY
          }) : {}),
          ...forcedEnv("LENGRVIS_BACKEND_URL", this.getBaseUrl()),
          ...forcedEnv("LENGRVIS_DESKTOP_API_TOKEN", this.desktopApiToken),
          ...forcedEnv("LENGRVIS_NATIVE_CONFIRMATION_PUBLIC_KEY", this.nativeConfirmationPublicKey),
          ...bundledOllamaEnv
        },
        windowsHide: true
      });
      this.child = child;

      child.stdout.on("data", createBackendProcessOutputLogHandler("stdout"));
      child.stderr.on("data", createBackendProcessOutputLogHandler("stderr"));

      child.once("exit", (code) => {
        void writeBackendLog(`backend process exited; code=${code}`);
        if (this.child !== child) {
          return;
        }
        this.child = null;
        this.controlTransport.invalidateIdentity();
        this.status = this.makeStatus(
          code === 0 ? "stopped" : "error",
          code === 0 ? "后端进程已停止" : `后端进程异常退出，代码 ${code}`
        );
      });

      child.once("error", (error) => {
        void writeBackendLog(`backend process error; message=${error.message}`);
        if (this.child !== child) {
          return;
        }
        this.child = null;
        this.controlTransport.invalidateIdentity();
        this.status = this.makeStatus("error", error.message);
      });

      return this.refreshStatus("starting", "Backend process started; waiting for health check");
    } catch (error) { // broad-exception-boundary
      this.controlTransport.invalidateIdentity();
      const message = error instanceof Error ? error.message : "无法启动后端进程";
      this.status = this.makeStatus("error", message);
      return this.status;
    }
  }

  stop(): Promise<BackendStatus> {
    return this.lifecycle.stop();
  }

  private async stopOperation(): Promise<BackendStatus> {
    if (!this.child || this.child.killed) {
      this.child = null;
      const message = this.status.message?.includes("Windows Service")
        ? "Windows Service 由系统托管，未停止"
        : "后端进程未运行";
      return this.refreshStatus("stopped", message);
    }

    const child = this.child;
    // Graceful drain before the hard kill: ask the backend to pause in-flight
    // runs (bounded wait) so they survive as resumable PAUSED rows instead of
    // crash-orphaned RUNNING zombies that startup recovery has to reconcile.
    const drainError = await this.controlTransport.setRuntimeMode(
      "background",
      "desktop_quit",
      10_000
    );
    if (drainError) {
      void writeBackendLog("backend drain before stop failed; remote detail omitted");
    }
    await terminateProcessTree(child);
    if (this.child === child) {
      this.child = null;
    }
    this.controlTransport.invalidateIdentity();
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
    const runtimeModeError = await this.controlTransport.setRuntimeMode("foreground", reason);
    const status = await this.getStatus();
    return runtimeModeError ? this.withRuntimeModeError(status, "foreground", runtimeModeError) : status;
  }

  async enterBackground(reason = "tray_background"): Promise<BackendStatus> {
    await this.start();
    const runtimeModeError = await this.controlTransport.setRuntimeMode("background", reason);
    const status = await this.getStatus();
    return runtimeModeError ? this.withRuntimeModeError(status, "background", runtimeModeError) : status;
  }

  async emergencyStop(): Promise<{ ok: boolean; [key: string]: unknown }> {
    return this.controlTransport.emergencyStop();
  }

  private async connectToWindowsService(service: WindowsServiceProbe): Promise<BackendStatus> {
    const serviceLabel = service.serviceName ? `Windows Service：${service.serviceName}` : "Windows Service";
    this.managedWindowsServiceName = service.serviceName ?? this.managedWindowsServiceName;
    await writeBackendLog(`windows service running; service=${service.serviceName ?? "<unknown>"}; probing ${this.getBaseUrl()}`);
    const health = await this.probeHealth();
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
    const health = await this.probeHealth();
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
    const { health, runtime } = await this.controlTransport.probeStatus();
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

  private async probeHealth(): Promise<NonNullable<BackendStatus["health"]>> {
    return this.controlTransport.probeHealth();
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

    const packagedBackend = join(process.resourcesPath, "backend", process.platform === "win32" ? "backend.exe" : "backend");
    if (existsSync(packagedBackend)) {
      return packagedBackend;
    }

    if (app.isPackaged) {
      return undefined;
    }

    const configuredCommand = env("LENGRVIS_BACKEND_COMMAND");
    if (configuredCommand) {
      return configuredCommand;
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

export function writeBackendLog(message: string): Promise<void> {
  const entry = `[${new Date().toISOString()}] ${truncateBackendLogMessage(message)}\n`;
  backendLogWriteQueue = backendLogWriteQueue
    .catch(() => undefined)
    .then(() => writeBackendLogEntry(entry));
  return backendLogWriteQueue;
}

export function createBackendProcessOutputLogHandler(
  channel: "stdout" | "stderr",
  writer: (message: string) => void | Promise<void> = writeBackendLog
): () => void {
  let noticeWritten = false;
  return () => {
    if (noticeWritten) return;
    noticeWritten = true;
    void writer(`[${channel}] output received; content omitted from persistent logs`);
  };
}

async function writeBackendLogEntry(entry: string): Promise<void> {
  try {
    const logDir = app.getPath("userData");
    await mkdir(logDir, { recursive: true });
    const logPath = join(logDir, "backend-process.log");
    await rotateBackendLogIfNeeded(logPath, Buffer.byteLength(entry, "utf8"));
    await appendFile(logPath, entry, { encoding: "utf8", mode: 0o600 });
  } catch {
    // Logging must never block app startup.
  }
}

function truncateBackendLogMessage(message: string): string {
  const redacted = redactBackendLogText(message);
  if (Buffer.byteLength(redacted, "utf8") <= BACKEND_LOG_MAX_ENTRY_BYTES) {
    return redacted;
  }
  const maxContentBytes = BACKEND_LOG_MAX_ENTRY_BYTES - Buffer.byteLength(BACKEND_LOG_TRUNCATION_MARKER, "utf8");
  return `${Buffer.from(redacted, "utf8").subarray(0, maxContentBytes).toString("utf8")}${BACKEND_LOG_TRUNCATION_MARKER}`;
}

async function rotateBackendLogIfNeeded(logPath: string, incomingBytes: number): Promise<void> {
  let currentBytes = 0;
  try {
    currentBytes = (await stat(logPath)).size;
  } catch {
    return;
  }
  if (currentBytes + incomingBytes <= BACKEND_LOG_MAX_BYTES) {
    return;
  }

  const rotatedPath = `${logPath}${BACKEND_LOG_ROTATED_SUFFIX}`;
  try {
    await rm(rotatedPath, { force: true });
    await rename(logPath, rotatedPath);
  } catch {
    await truncate(logPath, 0).catch(() => undefined);
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
