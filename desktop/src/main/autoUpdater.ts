import { app, dialog, shell } from "electron";

import { isVersionQuarantined, noteUpdateDownloaded } from "./updateHealthStore";
import { buildUpdateRecoveryMessage } from "./updateRollbackMessage";

/**
 * electron-updater 集成（GitHub Releases 通道，feed 来自打包时生成的 app-update.yml）。
 *
 * 设计约束：
 * - 仅在打包后的应用里激活；dev 模式没有 app-update.yml，直接进入 unsupported 状态。
 * - electron-updater 通过 require 延迟加载，依赖缺失时优雅降级而不是让主进程崩溃。
 * - 后端 backend.exe 位于安装包 resources 内，随安装包整体替换，更新即同时更新后端。
 * - 更新后的健康校验与回滚保护由 updateHealthStore 驱动（见 main.ts 启动流程）。
 */

export type UpdaterState =
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "ready"
  | "up-to-date"
  | "error"
  | "unsupported";

interface UpdaterStatus {
  state: UpdaterState;
  version: string | null;
  error: string | null;
}

const RELEASES_URL = "https://github.com/suli9710/-lengrvis/releases";

const status: UpdaterStatus = { state: "idle", version: null, error: null };

let cachedUpdater: ElectronAppUpdater | null = null;
let loadAttempted = false;
let stateListener: (() => void) | null = null;
let restartPromptShown = false;
let downloadPromptShown = false;

interface ElectronAppUpdater {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  allowDowngrade: boolean;
  on(event: string, listener: (...args: unknown[]) => void): void;
  checkForUpdates(): Promise<unknown>;
  downloadUpdate(): Promise<unknown>;
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
}

function setState(next: UpdaterState, patch?: Partial<UpdaterStatus>): void {
  status.state = next;
  if (patch && "version" in patch) {
    status.version = patch.version ?? null;
  }
  status.error = next === "error" ? (patch?.error ?? status.error) : null;
  stateListener?.();
}

function loadUpdater(): ElectronAppUpdater | null {
  if (cachedUpdater || loadAttempted) {
    return cachedUpdater;
  }
  loadAttempted = true;
  if (!app.isPackaged) {
    setState("unsupported");
    return null;
  }
  try {
    // 延迟加载：dev/未安装依赖时不拖垮主进程启动。
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { autoUpdater } = require("electron-updater") as { autoUpdater: ElectronAppUpdater };
    // 打包构建默认校验更新包签名（verifyUpdateCodeSignature: true）；
    // 未签名本地 dist 不会通过签名校验，因此不静默下载安装。
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.allowDowngrade = false;

    autoUpdater.on("checking-for-update", () => setState("checking"));
    autoUpdater.on("update-available", (...args: unknown[]) => {
      const info = args[0] as { version?: string } | undefined;
      const version = info?.version ?? null;
      // 已知崩溃版本：在更新版本超越它之前不再提示下载。
      if (isVersionQuarantined(version)) {
        setState("up-to-date", { version });
        return;
      }
      setState("available", { version });
      void promptDownload(version);
    });
    autoUpdater.on("update-not-available", () => setState("up-to-date"));
    autoUpdater.on("update-downloaded", (...args: unknown[]) => {
      const info = args[0] as { version?: string } | undefined;
      // 记录待安装版本，下次启动据此做健康校验与回滚保护。
      noteUpdateDownloaded(info?.version);
      setState("ready", { version: info?.version ?? null });
      void promptRestart(info?.version ?? null);
    });
    autoUpdater.on("error", (...args: unknown[]) => {
      const error = args[0];
      const message = error instanceof Error ? error.message : String(error);
      console.warn("Auto update failed:", message);
      setState("error", { error: message });
    });

    cachedUpdater = autoUpdater;
  } catch (error) { // broad-exception-boundary
    console.warn("electron-updater unavailable; auto update disabled:", error);
    setState("unsupported");
  }
  return cachedUpdater;
}

async function promptDownload(version: string | null): Promise<void> {
  if (downloadPromptShown) {
    return;
  }
  downloadPromptShown = true;
  try {
    const result = await dialog.showMessageBox({
      type: "info",
      title: "发现新版本",
      message: version ? `发现新版本 ${version}。` : "发现新版本。",
      detail: "是否下载更新？下载完成后会提示重启安装。",
      buttons: ["下载更新", "稍后"],
      defaultId: 0,
      cancelId: 1
    });
    if (result.response === 0 && cachedUpdater) {
      setState("downloading", { version });
      void cachedUpdater.downloadUpdate().catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        console.warn("Update download failed:", message);
        setState("error", { error: message });
      });
    }
  } finally {
    downloadPromptShown = false;
  }
}

async function promptRestart(version: string | null): Promise<void> {
  if (restartPromptShown) {
    return;
  }
  restartPromptShown = true;
  try {
    const result = await dialog.showMessageBox({
      type: "info",
      title: "更新已就绪",
      message: version ? `新版本 ${version} 已下载完成。` : "新版本已下载完成。",
      detail: "重启 Lengrvis 即可完成更新；也可以稍后退出时自动安装。",
      buttons: ["立即重启", "稍后"],
      defaultId: 0,
      cancelId: 1
    });
    if (result.response === 0) {
      cachedUpdater?.quitAndInstall();
    }
  } finally {
    restartPromptShown = false;
  }
}

/** 启动时静默检查一次更新（仅打包后生效）。 */
export function setupAutoUpdater(onStateChanged?: () => void): void {
  stateListener = onStateChanged ?? null;
  const updater = loadUpdater();
  if (!updater) {
    return;
  }
  void updater.checkForUpdates().catch((error: unknown) => {
    console.warn("Startup update check failed:", error);
  });
}

/** 托盘菜单触发的手动检查。 */
export function checkForUpdatesInteractive(): void {
  const updater = loadUpdater();
  if (!updater) {
    return;
  }
  if (status.state === "checking" || status.state === "downloading") {
    return;
  }
  if (status.state === "available") {
    void promptDownload(status.version);
    return;
  }
  if (status.state === "ready") {
    void promptRestart(status.version);
    return;
  }
  void updater.checkForUpdates().catch((error: unknown) => {
    console.warn("Manual update check failed:", error);
  });
}

/**
 * 进入回滚保护模式：检测到更新后连续启动失败时调用。
 *
 * 说明：electron-updater 无法静默重装一个更旧的构建（GitHub feed 仍指向最新、
 * 现已被隔离的发行版），因此真正的「自动降级」在客户端不可行。我们采取可行且
 * 诚实的做法——停止自动安装问题版本、放开 allowDowngrade 以便用户手动恢复，
 * 并引导用户回到上一个稳定版本，从而保证可用性而不是反复崩溃。
 */
export async function enterUpdateRollbackMode(
  quarantinedVersion: string | null,
  lastGoodVersion: string | null
): Promise<void> {
  const updater = loadUpdater();
  if (updater) {
    updater.autoInstallOnAppQuit = false;
    updater.allowDowngrade = true;
  }
  setState("error", {
    error: quarantinedVersion
      ? `版本 ${quarantinedVersion} 启动异常，已暂停自动安装`
      : "更新启动异常，已暂停自动安装"
  });
  const recoveryMessage = buildUpdateRecoveryMessage(quarantinedVersion, lastGoodVersion);
  const result = await dialog.showMessageBox({
    type: "warning",
    ...recoveryMessage,
    buttons: ["查看历史版本", "稍后"],
    defaultId: 0,
    cancelId: 1
  });
  if (result.response === 0) {
    void shell.openExternal(RELEASES_URL);
  }
}

export function getUpdaterStatus(): UpdaterStatus {
  return { ...status };
}

/** 托盘菜单展示用的状态文案；返回 null 表示当前环境不支持自动更新。 */
export function describeUpdaterForTray(): { label: string; enabled: boolean } | null {
  switch (status.state) {
    case "unsupported":
      return null;
    case "checking":
      return { label: "正在检查更新…", enabled: false };
    case "available":
      return { label: status.version ? `下载更新 ${status.version}` : "下载更新", enabled: true };
    case "downloading":
      return { label: status.version ? `正在下载 ${status.version}…` : "正在下载更新…", enabled: false };
    case "ready":
      return { label: status.version ? `重启以更新到 ${status.version}` : "重启以完成更新", enabled: true };
    case "error":
      return { label: "检查更新（上次失败，点击重试）", enabled: true };
    case "up-to-date":
      return { label: `检查更新（当前 ${app.getVersion()} 已最新）`, enabled: true };
    case "idle":
    default:
      return { label: "检查更新", enabled: true };
  }
}
