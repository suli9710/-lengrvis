import { app } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { appendFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { cwd as getCwd } from "node:process";

import type { BackendStatus } from "../shared/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const HEALTH_ENDPOINT = "/health";

export interface BackendProcessOptions {
  baseUrl?: string;
  command?: string;
  args?: string[];
  cwd?: string;
}

export class BackendProcessManager {
  private child: ChildProcessWithoutNullStreams | null = null;
  private status: BackendStatus;

  constructor(private readonly options: BackendProcessOptions = {}) {
    this.status = {
      state: "stopped",
      baseUrl: this.getBaseUrl(),
      lastCheckedAt: new Date().toISOString()
    };
  }

  getBaseUrl(): string {
    return this.options.baseUrl ?? process.env.MAVRIS_BACKEND_URL ?? DEFAULT_BACKEND_URL;
  }

  async start(): Promise<BackendStatus> {
    if (this.child && !this.child.killed) {
      return this.refreshStatus("running", "后端进程已在运行");
    }

    const command = this.resolveBackendCommand();
    const args = this.options.args ?? splitArgs(process.env.MAVRIS_BACKEND_ARGS);
    await writeBackendLog(`start requested; command=${command ?? "<none>"} args=${JSON.stringify(args)} resourcesPath=${process.resourcesPath} appPath=${app.getAppPath()} isPackaged=${app.isPackaged} defaultApp=${String(process.defaultApp)}`);

    if (!command) {
      return this.refreshStatus(
        "not_configured",
        "未配置后端命令，将使用外部后端地址"
      );
    }

    this.status = this.makeStatus("starting", "正在启动后端进程");

    try {
      this.child = spawn(command, args, {
        cwd: this.options.cwd ?? process.env.MAVRIS_BACKEND_CWD ?? dirname(command),
        env: {
          ...process.env,
          MARVIS_CONFIG_DIR: process.env.MARVIS_CONFIG_DIR ?? resolveConfigDir(command),
          MAVRIS_BACKEND_URL: this.getBaseUrl()
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

      return this.refreshStatus("running", "后端进程已启动");
    } catch (error) {
      const message = error instanceof Error ? error.message : "无法启动后端进程";
      this.status = this.makeStatus("error", message);
      return this.status;
    }
  }

  async stop(): Promise<BackendStatus> {
    if (!this.child || this.child.killed) {
      this.child = null;
      return this.refreshStatus("stopped", "后端进程未运行");
    }

    this.child.kill();
    this.child = null;
    return this.refreshStatus("stopped", "后端进程已停止");
  }

  async getStatus(): Promise<BackendStatus> {
    const processState = this.child && !this.child.killed ? "running" : this.status.state;
    return this.refreshStatus(processState, this.status.message);
  }

  private async refreshStatus(
    fallbackState: BackendStatus["state"],
    fallbackMessage?: string
  ): Promise<BackendStatus> {
    const health = await probeHealth(this.getBaseUrl());
    const hasConfiguredCommand = Boolean(this.resolveBackendCommand());

    if (health.ok) {
      this.status = this.makeStatus("running", "后端已连接", health);
    } else if (!hasConfiguredCommand && fallbackState !== "error") {
      this.status = this.makeStatus(
        "not_configured",
        fallbackMessage ?? "等待外部后端",
        health
      );
    } else {
      this.status = this.makeStatus(fallbackState, fallbackMessage, health);
    }

    return this.status;
  }

  private makeStatus(
    state: BackendStatus["state"],
    message?: string,
    health?: BackendStatus["health"]
  ): BackendStatus {
    return {
      state,
      baseUrl: this.getBaseUrl(),
      pid: this.child?.pid,
      message,
      health,
      lastCheckedAt: new Date().toISOString()
    };
  }

  private resolveBackendCommand(): string | undefined {
    if (this.options.command) {
      return this.options.command;
    }

    if (process.env.MAVRIS_BACKEND_COMMAND) {
      return process.env.MAVRIS_BACKEND_COMMAND;
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
}

function resolveConfigDir(command: string): string {
  const candidates = [
    getCwd(),
    app.getAppPath(),
    join(process.resourcesPath, "..", ".."),
    join(dirname(command), "..", "..", "..", "..")
  ];

  const match = candidates.find((candidate) => existsSync(join(candidate, ".env")) || existsSync(join(candidate, "config.yaml")));
  return match ?? getCwd();
}

async function writeBackendLog(message: string): Promise<void> {
  try {
    const logDir = app.getPath("userData");
    await mkdir(logDir, { recursive: true });
    await appendFile(join(logDir, "backend-process.log"), `[${new Date().toISOString()}] ${message}\n`, "utf8");
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

async function probeHealth(baseUrl: string): Promise<NonNullable<BackendStatus["health"]>> {
  const startedAt = Date.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1500);

  try {
    const response = await fetch(new URL(HEALTH_ENDPOINT, baseUrl), {
      method: "GET",
      signal: controller.signal
    });

    return {
      ok: response.ok,
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
