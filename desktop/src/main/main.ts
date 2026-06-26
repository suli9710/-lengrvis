import {
  app,
  BrowserWindow,
  globalShortcut,
  Menu,
  Tray,
  nativeImage,
  session,
  shell,
  type MenuItemConstructorOptions,
  type PermissionCheckHandlerHandlerDetails,
  type PermissionRequest,
  type WebContents
} from "electron";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import type { BackendStatus } from "../shared/types";
import {
  checkForUpdatesInteractive,
  describeUpdaterForTray,
  enterUpdateRollbackMode,
  setupAutoUpdater
} from "./autoUpdater";
import { BackendProcessManager } from "./backendProcess";
import { BrowserHost, BrowserHostWebSocketBridge } from "./browserHost";
import { registerConsentIpcHandlers } from "./consentManager";
import { setupCrashReporter } from "./crashReporter";
import { registerDesktopWebSocketIpcHandlers } from "./desktopWebSocket";
import { isSafeExternalUrl, registerIpcHandlers } from "./ipc";
import { NotificationBridge } from "./notifications";
import { closeLaunch, confirmHealthy, getLastGoodVersion, reconcileOnStartup } from "./updateHealthStore";

const devServerUrl = app.isPackaged ? "" : process.env.VITE_DEV_SERVER_URL;
const isDev = Boolean(devServerUrl);
const BACKEND_STATUS_POLL_MS = 60_000;
// 更新后保持稳定运行多久才判定为健康（并据此把待验证版本提升为 last-good）。
const HEALTHY_AFTER_MS = 60_000;
const GLOBAL_TOGGLE_SHORTCUT = "CommandOrControl+Alt+L";
const TRAY_ICON_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAI/SURBVFhH1VcxTwIxGGV0dHTDxFZXR0d/ghtuaMvg6OiG8Q84urEwODrq5kLiaJgcSVg4rgTCQIhhqPlKr7RfW+7OHCa+5EWOe/R7fe199Wq1/wrSSs6OuDi3iTWVot4cHxCW3FAmXikXMkomPsj1+O6kOTrEY/wK9eZsn3JxT7lYesVyOXkC43jMwoBYKU9n/sCluKQsvcRj54Ky8dXvZh3lPa4RhS6OB6iCj7iWh3Xslc7cIWxkXNMANkwFa57HJW2NT3FtBYgo8APZ7ksHw7epp6EPCzm0RclCNrBGk/DJC66tZx+OHhsIDd54W+VqHOIUoHl4ImwgWelZrmT3wdZMZTdZfz9UfwsY4JMnxwB0MF8UM4CWwcT/LXtGm2dAjExx3e2wIGBgIbuBAib+/tzR5hjYLAMcLN7NiIFG51tfZMuQxS9lr7P5XMhA1iGPWXLh3YwZ4HPZ05dqGaz425aZIgbIdXq7TgBOuYAgbMC9blvx22kUMWDaM0QRuBk1QM0ybDZdrwPacgbgyVMGdPv1BFEDdiEFiL+8AThz9FOgmpAviBpAjUfFX94AbH79INZqhIsBFmwzYLfedfylDSzrzcGeMRA7B3ZF7zyApoBFuyQ8+o4BALjCwl2QMPGJayv8VQrB2WfYdipWQ3QKhkC4ePZ/WAGZ+HB2fgwgqtyEKj7bx7W2Qr+Q+IOVJrygFJh5CKpNs/TdHzSfhKdfWzdcGcBAelmC/zc6hPfHrM/vApCKflpgiQzh+7JR/wBFmasNoNL4MAAAAABJRU5ErkJggg==";
const backend = new BackendProcessManager();
const browserHost = new BrowserHost(() => mainWindow);
const browserHostBridge = new BrowserHostWebSocketBridge(
  browserHost,
  () => backend.getBaseUrl(),
  () => backend.getDesktopApiToken()
);
const notifications = new NotificationBridge({
  backend,
  getMainWindow: () => mainWindow
});

function getPackagedBackendName(): string {
  return process.platform === "win32" ? "backend.exe" : "backend";
}

function isPortableMode(): boolean {
  return existsSync(join(process.resourcesPath, "backend", getPackagedBackendName()));
}

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let latestBackendStatus: BackendStatus | null = null;
let backendStatusTimer: NodeJS.Timeout | null = null;
let healthConfirmTimer: NodeJS.Timeout | null = null;
let isQuitting = false;
let backgroundTransition: Promise<void> | null = null;

function hardenDefaultSessionPermissions(): void {
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    callback(isAllowedRendererPermission(webContents, permission, details));
  });
  session.defaultSession.setPermissionCheckHandler((webContents, permission, _requestingOrigin, details) => {
    return isAllowedRendererPermission(webContents, permission, details);
  });
}

function isAllowedRendererPermission(
  webContents: WebContents | null,
  permission: string,
  details?: PermissionRequest | PermissionCheckHandlerHandlerDetails
): boolean {
  if (!mainWindow || mainWindow.isDestroyed() || webContents?.id !== mainWindow.webContents.id) {
    return false;
  }

  if (permission === "clipboard-sanitized-write") {
    return true;
  }

  if (permission !== "media") {
    return false;
  }

  const mediaTypes = getRequestedMediaTypes(details);
  return mediaTypes.length === 1 && mediaTypes[0] === "audio";
}

function getRequestedMediaTypes(details?: PermissionRequest | PermissionCheckHandlerHandlerDetails): string[] {
  if (!details) {
    return [];
  }
  if ("mediaTypes" in details && Array.isArray(details.mediaTypes)) {
    return details.mediaTypes;
  }
  if ("mediaType" in details && typeof details.mediaType === "string") {
    return [details.mediaType];
  }
  return [];
}

function createMainWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1120,
    minHeight: 720,
    title: "Lengrvis",
    backgroundColor: "#f4f6f8",
    show: false,
    webPreferences: {
      preload: join(__dirname, "../preload/preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  window.once("ready-to-show", () => {
    window.show();
  });

  window.on("close", (event) => {
    if (isQuitting) {
      return;
    }
    event.preventDefault();
    void enterTrayBackground();
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalUrl(url)) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (isDev && url === devServerUrl) {
      return;
    }
    if (!isDev && url.startsWith(rendererFileUrl())) {
      return;
    }
    event.preventDefault();
    if (isSafeExternalUrl(url)) {
      void shell.openExternal(url);
    }
  });

  if (isDev && devServerUrl) {
    window.loadURL(devServerUrl);
    window.webContents.openDevTools({ mode: "detach" });
  } else {
    window.loadFile(join(__dirname, "../renderer/index.html"));
  }

  return window;
}

function rendererFileUrl(): string {
  return pathToFileURL(join(__dirname, "../renderer/")).toString();
}

function createTray(): void {
  if (tray) {
    return;
  }

  const image = nativeImage.createFromDataURL(TRAY_ICON_DATA_URL).resize({ width: 16, height: 16 });
  tray = new Tray(image);
  tray.setToolTip("Lengrvis");
  tray.on("click", showMainWindow);
  rebuildTrayMenu();
}

function showMainWindow(): void {
  void enterForegroundAndShow();
}

async function enterForegroundAndShow(): Promise<void> {
  latestBackendStatus = await backend.enterForeground("desktop_opened");
  rebuildTrayMenu();
  browserHostBridge.start();

  if (!mainWindow || mainWindow.isDestroyed()) {
    mainWindow = createMainWindow();
  }

  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
}

async function enterTrayBackground(): Promise<void> {
  if (backgroundTransition) {
    return backgroundTransition;
  }
  backgroundTransition = (async () => {
    browserHostBridge.stop();
    browserHost.destroy();
    mainWindow?.hide();
    latestBackendStatus = await backend.enterBackground("window_hidden_to_tray");
    rebuildTrayMenu();
  })();
  try {
    await backgroundTransition;
  } finally {
    backgroundTransition = null;
  }
}

function rebuildTrayMenu(): void {
  if (!tray) {
    return;
  }

  const status = latestBackendStatus;
  const statusText = status ? formatBackendStatus(status) : "后端：检查中";
  const template: MenuItemConstructorOptions[] = [
    {
      label: statusText,
      enabled: false
    },
    {
      label: "打开 Lengrvis",
      sublabel: GLOBAL_TOGGLE_SHORTCUT.replace("CommandOrControl", "Ctrl"),
      click: showMainWindow
    },
    {
      label: "刷新连接状态",
      click: () => {
        void refreshTrayBackendStatus();
      }
    },
    { type: "separator" },
    {
      label: "开机自动启动",
      type: "checkbox",
      checked: isOpenAtLoginEnabled(),
      click: (menuItem) => {
        setOpenAtLogin(menuItem.checked);
      }
    },
    ...buildUpdaterMenuItems(),
    { type: "separator" },
    {
      label: "退出",
      click: () => {
        isQuitting = true;
        app.quit();
      }
    }
  ];

  tray.setToolTip(`Lengrvis - ${statusText}`);
  tray.setContextMenu(Menu.buildFromTemplate(template));
}

function buildUpdaterMenuItems(): MenuItemConstructorOptions[] {
  const updater = describeUpdaterForTray();
  if (!updater) {
    return [];
  }
  return [
    {
      label: updater.label,
      enabled: updater.enabled,
      click: () => {
        checkForUpdatesInteractive();
      }
    }
  ];
}

function toggleMainWindow(): void {
  if (mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible() && mainWindow.isFocused()) {
    void enterTrayBackground();
    return;
  }
  showMainWindow();
}

function registerGlobalShortcut(): void {
  try {
    const registered = globalShortcut.register(GLOBAL_TOGGLE_SHORTCUT, toggleMainWindow);
    if (!registered) {
      console.warn(`Global shortcut ${GLOBAL_TOGGLE_SHORTCUT} is taken by another app; skipping.`);
    }
  } catch (error) {
    console.warn("Failed to register global shortcut:", error);
  }
}

/**
 * 更新后启动监控：保持稳定运行 HEALTHY_AFTER_MS 后，将当前运行版本判定为健康，
 * 并把待验证的更新版本提升为 last-good（回滚参照）。若期间崩溃/挂起，定时器不会触发，
 * 下次启动会被识别为一次失败启动。
 */
function scheduleHealthConfirmation(): void {
  if (healthConfirmTimer) {
    return;
  }
  healthConfirmTimer = setTimeout(() => {
    healthConfirmTimer = null;
    confirmHealthy(app.getVersion());
  }, HEALTHY_AFTER_MS);
}

function isOpenAtLoginEnabled(): boolean {
  try {
    return app.getLoginItemSettings().openAtLogin;
  } catch {
    return false;
  }
}

function setOpenAtLogin(enabled: boolean): void {
  try {
    app.setLoginItemSettings({ openAtLogin: enabled, args: ["--hidden"] });
  } catch (error) {
    console.warn("Failed to update login item settings:", error);
  }
  rebuildTrayMenu();
}

async function refreshTrayBackendStatus(): Promise<void> {
  const status = await backend.getStatus();
  latestBackendStatus = status;
  rebuildTrayMenu();
}

function startTrayBackendStatusPolling(): void {
  if (backendStatusTimer) {
    return;
  }

  void refreshTrayBackendStatus();
  backendStatusTimer = setInterval(() => {
    void refreshTrayBackendStatus();
  }, BACKEND_STATUS_POLL_MS);
}

function stopTrayBackendStatusPolling(): void {
  if (!backendStatusTimer) {
    return;
  }

  clearInterval(backendStatusTimer);
  backendStatusTimer = null;
}

function formatBackendStatus(status: BackendStatus): string {
  const health = status.health?.ok ? "已连接" : "未连接";
  const latency = typeof status.health?.latencyMs === "number" && status.health.ok
    ? ` · ${status.health.latencyMs}ms`
    : "";
  return `后端：${backendStateLabel(status.state)} · ${health}${latency}`;
}

function backendStateLabel(state: BackendStatus["state"]): string {
  switch (state) {
    case "running":
      return "运行中";
    case "starting":
      return "启动中";
    case "not_configured":
      return "未配置";
    case "error":
      return "异常";
    case "stopped":
    default:
      return "已停止";
  }
}

app.setName("Lengrvis");
if (process.platform === "win32") {
  app.setAppUserModelId("Lengrvis");
}

// 尽早启动崩溃采集，确保后续任何进程崩溃都能被记录。
setupCrashReporter();

const gotSingleInstanceLock = app.requestSingleInstanceLock();

if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    showMainWindow();
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    hardenDefaultSessionPermissions();
    registerIpcHandlers(backend);
    registerDesktopWebSocketIpcHandlers(backend);
    registerConsentIpcHandlers();
    browserHost.registerIpcHandlers();
    notifications.registerIpcHandlers();
    mainWindow = createMainWindow();
    createTray();
    registerGlobalShortcut();
    // 先对照持久化的健康记录核对当前运行版本，再启动自动更新检查。
    const startupHealth = reconcileOnStartup(app.getVersion());
    setupAutoUpdater(() => {
      rebuildTrayMenu();
    });
    if (startupHealth.action === "quarantine") {
      // 更新版本连续启动失败：进入回滚保护并引导用户恢复稳定版本。
      void enterUpdateRollbackMode(startupHealth.quarantinedVersion, getLastGoodVersion());
    } else if (startupHealth.action === "monitor") {
      // 正在验证一次（可能是刚更新的）启动：稳定运行后再判定为健康。
      scheduleHealthConfirmation();
    }
    notifications.startBackendListener();

    if (!process.defaultApp || app.isPackaged || isPortableMode() || backendAutostartEnabled()) {
      latestBackendStatus = await backend.start();
      rebuildTrayMenu();
    }
    if (startHiddenRequested()) {
      // Login-item launch: stay in the tray until the user summons the window.
      await enterTrayBackground();
    } else {
      await enterForegroundAndShow();
    }
    startTrayBackendStatusPolling();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        showMainWindow();
      }
    });
  });

  app.on("window-all-closed", () => {
    if (isQuitting && process.platform !== "darwin") {
      app.quit();
    }
  });

  app.on("will-quit", () => {
    globalShortcut.unregisterAll();
    // 干净退出：关闭启动信标，避免一次正常的短会话在下次启动被误判为崩溃。
    closeLaunch();
  });

  let backendCleanupDone = false;
  let backendCleanupInProgress = false;
  app.on("before-quit", (event) => {
    isQuitting = true;
    if (backendCleanupDone) {
      return;
    }
    if (backendCleanupInProgress) {
      event.preventDefault();
      return;
    }
    // Electron does not await async before-quit listeners; preventDefault
    // keeps the app alive until backend.stop() finishes.
    event.preventDefault();
    backendCleanupInProgress = true;
    void (async () => {
      stopTrayBackendStatusPolling();
      notifications.stopBackendListener();
      browserHostBridge.stop();
      browserHost.destroy();
      try {
        await backend.stop();
      } finally {
        backendCleanupDone = true;
        backendCleanupInProgress = false;
        app.quit();
      }
    })();
  });
}

function backendAutostartEnabled(): boolean {
  return process.env.LENGRVIS_BACKEND_AUTOSTART === "1";
}

function startHiddenRequested(): boolean {
  return process.argv.includes("--hidden") || app.getLoginItemSettings().wasOpenedAsHidden;
}
