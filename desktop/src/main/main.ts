import {
  app,
  BrowserWindow,
  globalShortcut,
  Menu,
  Tray,
  nativeImage,
  powerMonitor,
  screen,
  session,
  type MenuItemConstructorOptions,
  type PermissionCheckHandlerHandlerDetails,
  type PermissionRequest,
  type WebContents
} from "electron";
import { existsSync } from "node:fs";
import { join } from "node:path";

import type { BackendStatus } from "../shared/types";
import {
  ApprovalSessionGenerationInvalidationError,
  ApprovalSessionVisibilityCoordinator,
  registerApprovalSessionPowerRotation,
  type ApprovalSessionPowerEvent
} from "./approvalSessionGeneration";
import {
  checkForUpdatesInteractive,
  describeUpdaterForTray,
  enterUpdateRollbackMode,
  setupAutoUpdater
} from "./autoUpdater";
import { BackendProcessManager } from "./backendProcess";
import { BrowserHost, BrowserHostWebSocketBridge } from "./browserHost";
import { emergencyStopAgentWork, GLOBAL_EMERGENCY_STOP_SHORTCUT } from "./emergencyStop";
import { registerConsentIpcHandlers } from "./consentManager";
import { setupCrashReporter } from "./crashReporter";
import { registerDesktopWebSocketIpcHandlers } from "./desktopWebSocket";
import { openSafeExternalUrl } from "./externalUrl";
import { registerIpcHandlers } from "./ipc";
import {
  registerMainWindowConstraintListeners,
  registerMainWindowDisplayConstraintListeners
} from "./mainWindowDisplayConstraints";
import {
  fitMainWindowToWorkArea,
  minimumMainWindowSize
} from "./mainWindowSizing";
import { NotificationBridge } from "./notifications";
import {
  isPackagedRendererEntryUrl,
  PACKAGED_RENDERER_ENTRY_URL,
  registerPackagedRendererProtocol,
  registerRendererSchemePrivileges
} from "./rendererProtocol";
import { advanceUpdateHealthWindow } from "./updateHealthGate";
import { closeLaunch, confirmHealthy, getLastGoodVersion, reconcileOnStartup } from "./updateHealthStore";

const devServerUrl = app.isPackaged ? "" : process.env.VITE_DEV_SERVER_URL;
const isDev = Boolean(devServerUrl);
const BACKEND_STATUS_POLL_MS = 60_000;
// 更新后保持稳定运行多久才判定为健康（并据此把待验证版本提升为 last-good）。
const HEALTHY_AFTER_MS = 60_000;
const HEALTH_CHECK_INTERVAL_MS = 5_000;
const GLOBAL_TOGGLE_SHORTCUT = "CommandOrControl+Alt+L";
const TRAY_ICON_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAI/SURBVFhH1VcxTwIxGGV0dHTDxFZXR0d/ghtuaMvg6OiG8Q84urEwODrq5kLiaJgcSVg4rgTCQIhhqPlKr7RfW+7OHCa+5EWOe/R7fe199Wq1/wrSSs6OuDi3iTWVot4cHxCW3FAmXikXMkomPsj1+O6kOTrEY/wK9eZsn3JxT7lYesVyOXkC43jMwoBYKU9n/sCluKQsvcRj54Ky8dXvZh3lPa4RhS6OB6iCj7iWh3Xslc7cIWxkXNMANkwFa57HJW2NT3FtBYgo8APZ7ksHw7epp6EPCzm0RclCNrBGk/DJC66tZx+OHhsIDd54W+VqHOIUoHl4ImwgWelZrmT3wdZMZTdZfz9UfwsY4JMnxwB0MF8UM4CWwcT/LXtGm2dAjExx3e2wIGBgIbuBAib+/tzR5hjYLAMcLN7NiIFG51tfZMuQxS9lr7P5XMhA1iGPWXLh3YwZ4HPZ05dqGaz425aZIgbIdXq7TgBOuYAgbMC9blvx22kUMWDaM0QRuBk1QM0ybDZdrwPacgbgyVMGdPv1BFEDdiEFiL+8AThz9FOgmpAviBpAjUfFX94AbH79INZqhIsBFmwzYLfedfylDSzrzcGeMRA7B3ZF7zyApoBFuyQ8+o4BALjCwl2QMPGJayv8VQrB2WfYdipWQ3QKhkC4ePZ/WAGZ+HB2fgwgqtyEKj7bx7W2Qr+Q+IOVJrygFJh5CKpNs/TdHzSfhKdfWzdcGcBAelmC/zc6hPfHrM/vApCKflpgiQzh+7JR/wBFmasNoNL4MAAAAABJRU5ErkJggg==";

registerRendererSchemePrivileges();

app.setName("Lengrvis");
if (process.platform === "win32") {
  app.setAppUserModelId("Lengrvis");
}

const gotSingleInstanceLock = app.requestSingleInstanceLock();
const backend = new BackendProcessManager();
const approvalSessionVisibility = new ApprovalSessionVisibilityCoordinator({
  activate: () => backend.activateApprovalSessionGeneration(),
  deactivate: () => backend.deactivateApprovalSessionGeneration()
});
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
let disposeApprovalSessionPowerRotation: (() => void) | null = null;
let disposeMainWindowDisplayConstraintListeners: (() => void) | null = null;
let tray: Tray | null = null;
let latestBackendStatus: BackendStatus | null = null;
let backendStatusTimer: NodeJS.Timeout | null = null;
let healthConfirmTimer: NodeJS.Timeout | null = null;
let updateHealthySince: number | null = null;
let healthConfirmationGeneration = 0;
let isQuitting = false;
let approvalSessionRotationFailed = false;

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
  const initialSize = fitMainWindowToWorkArea(screen.getPrimaryDisplay().workAreaSize);
  const minimumSize = minimumMainWindowSize(initialSize);
  const window = new BrowserWindow({
    ...initialSize,
    minWidth: minimumSize.width,
    minHeight: minimumSize.height,
    center: true,
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
    if (!startHiddenRequested() && approvalSessionVisibility.isForegroundRequested()) {
      window.show();
    }
  });

  window.on("close", (event) => {
    if (isQuitting) {
      return;
    }
    event.preventDefault();
    void enterTrayBackground();
  });

  const disposeDisplayConstraintListeners = registerMainWindowConstraintListeners(window, screen);
  window.once("closed", disposeDisplayConstraintListeners);

  window.webContents.setWindowOpenHandler(({ url }) => {
    void openSafeExternalUrl(url).catch(() => undefined);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (isDev && url === devServerUrl) {
      return;
    }
    if (!isDev && isPackagedRendererEntryUrl(url)) {
      return;
    }
    event.preventDefault();
    void openSafeExternalUrl(url).catch(() => undefined);
  });

  if (isDev && devServerUrl) {
    window.loadURL(devServerUrl);
    window.webContents.openDevTools({ mode: "detach" });
  } else {
    window.loadURL(PACKAGED_RENDERER_ENTRY_URL);
  }

  return window;
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
  let backendTransitionFailed = false;
  let shouldShow = false;
  try {
    shouldShow = await approvalSessionVisibility.enterForeground(async () => {
      try {
        latestBackendStatus = await backend.enterForeground("desktop_opened");
      } catch (error) { // broad-exception-boundary: classify the backend failure, then rethrow unchanged.
        backendTransitionFailed = true;
        throw error;
      }
    });
  } catch (error) { // broad-exception-boundary: keep the window hidden when foreground rotation fails.
    if (backendTransitionFailed) {
      console.warn("Could not enter foreground runtime mode:", error);
      return;
    }
    handleApprovalSessionRotationFailure("tray-foreground", error);
    return;
  }
  if (!shouldShow) return;
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
  let backendTransitionFailed = false;
  let transition: Promise<boolean>;
  try {
    transition = approvalSessionVisibility.enterBackground(async () => {
      try {
        latestBackendStatus = await backend.enterBackground("window_hidden_to_tray");
        rebuildTrayMenu();
      } catch (error) { // broad-exception-boundary: classify the backend failure, then rethrow unchanged.
        backendTransitionFailed = true;
        throw error;
      }
    });
  } catch (error) { // broad-exception-boundary: rotation already fails closed before the window is hidden.
    handleApprovalSessionRotationFailure("tray-background", error);
    return;
  }
  // Signing has already been synchronously revoked at this point.
  browserHostBridge.stop();
  browserHost.destroy();
  mainWindow?.hide();
  try {
    await transition;
  } catch (error) { // broad-exception-boundary: signing is revoked; normalize the async transition failure.
    if (backendTransitionFailed) {
      console.warn("Could not enter tray background runtime mode:", error);
      return;
    }
    handleApprovalSessionRotationFailure("tray-background", error);
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
    {
      label: `紧急停止所有 Agent 工作 (${GLOBAL_EMERGENCY_STOP_SHORTCUT.replace("CommandOrControl", "Ctrl")})`,
      click: () => {
        void triggerEmergencyStop();
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
    const emergencyRegistered = globalShortcut.register(GLOBAL_EMERGENCY_STOP_SHORTCUT, () => {
      void triggerEmergencyStop();
    });
    if (!emergencyRegistered) {
      console.warn(`Global shortcut ${GLOBAL_EMERGENCY_STOP_SHORTCUT} is taken by another app; skipping.`);
    }
  } catch (error) { // broad-exception-boundary
    console.warn("Failed to register global shortcut:", error);
  }
}

async function triggerEmergencyStop(): Promise<void> {
  const result = await emergencyStopAgentWork(browserHost, backend);
  if (!result.ok) {
    console.warn("Emergency stop completed with incomplete cancellation", result);
  }
  latestBackendStatus = await backend.getStatus().catch(() => latestBackendStatus);
  rebuildTrayMenu();
}

/**
 * 更新后启动监控：只有后端健康检查和已渲染的 React 根节点持续通过
 * HEALTHY_AFTER_MS，才把当前版本提升为 last-good。任一端不健康都会重置稳定窗口。
 */
function scheduleHealthConfirmation(): void {
  if (healthConfirmTimer) {
    return;
  }
  healthConfirmTimer = setTimeout(() => {
    void evaluateHealthConfirmation();
  }, 0);
}

async function evaluateHealthConfirmation(): Promise<void> {
  const generation = healthConfirmationGeneration;
  healthConfirmTimer = null;
  const [backendResult, rendererResult] = await Promise.allSettled([
    backend.getStatus(),
    isRendererHealthy()
  ]);
  // A failed probe must never count as health evidence. allSettled also keeps
  // a transient backend failure from abandoning the monitor loop entirely.
  const backendStatus = backendResult.status === "fulfilled" ? backendResult.value : null;
  if (backendResult.status === "fulfilled") {
    latestBackendStatus = backendResult.value;
  } else {
    console.warn("Update health backend probe failed:", backendResult.reason);
  }
  const rendererHealthy = rendererResult.status === "fulfilled" && rendererResult.value === true;

  // before-quit can run while a probe is in flight. Do not let that stale
  // result promote the version after the health gate was stopped.
  if (generation !== healthConfirmationGeneration || isQuitting) {
    return;
  }
  const window = advanceUpdateHealthWindow(
    updateHealthySince,
    {
      backendHealthy: backendStatus?.state === "running" && backendStatus.health?.ok === true,
      rendererHealthy
    },
    Date.now(),
    HEALTHY_AFTER_MS
  );
  updateHealthySince = window.healthySince;

  if (window.ready) {
    confirmHealthy(app.getVersion());
    return;
  }
  if (!isQuitting) {
    healthConfirmTimer = setTimeout(() => {
      void evaluateHealthConfirmation();
    }, HEALTH_CHECK_INTERVAL_MS);
  }
}

async function isRendererHealthy(): Promise<boolean> {
  const window = mainWindow;
  if (!window || window.isDestroyed() || window.webContents.isDestroyed() || window.webContents.isCrashed()) {
    return false;
  }
  try {
    return await window.webContents.executeJavaScript(
      'document.readyState === "complete" && Boolean(document.getElementById("root")?.childElementCount)',
      true
    ) === true;
  } catch {
    return false;
  }
}

function stopHealthConfirmation(): void {
  healthConfirmationGeneration += 1;
  if (healthConfirmTimer) {
    clearTimeout(healthConfirmTimer);
    healthConfirmTimer = null;
  }
  updateHealthySince = null;
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
  } catch (error) { // broad-exception-boundary
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

// 尽早启动崩溃采集，确保后续任何进程崩溃都能被记录。
setupCrashReporter();

if (!gotSingleInstanceLock) {
  app.quit();
} else {
  backend.initializeApprovalSessionGeneration();
  if (startHiddenRequested()) {
    // Login-item startup is background from the first synchronous boundary:
    // do not leave a signing generation active while backend/window startup awaits.
    backend.deactivateApprovalSessionGeneration();
  }
  app.on("second-instance", () => {
    showMainWindow();
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    if (!isDev) {
      registerPackagedRendererProtocol(join(__dirname, "../renderer"));
    }
    hardenDefaultSessionPermissions();
    disposeApprovalSessionPowerRotation = registerApprovalSessionPowerRotation(
      powerMonitor,
      () => backend.rotateApprovalSessionGeneration(),
      handleApprovalSessionRotationFailure
    );
    registerIpcHandlers(backend, browserHost);
    registerDesktopWebSocketIpcHandlers(backend);
    registerConsentIpcHandlers();
    browserHost.registerIpcHandlers();
    notifications.registerIpcHandlers();
    mainWindow = createMainWindow();
    disposeMainWindowDisplayConstraintListeners = registerMainWindowDisplayConstraintListeners(
      screen,
      () => mainWindow
    );
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
    if (startupHealth.action === "monitor") {
      // 后端启动且渲染器开始加载后，再启动双端持续健康监控。
      scheduleHealthConfirmation();
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
    disposeApprovalSessionPowerRotation?.();
    disposeApprovalSessionPowerRotation = null;
    disposeMainWindowDisplayConstraintListeners?.();
    disposeMainWindowDisplayConstraintListeners = null;
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
      stopHealthConfirmation();
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

function handleApprovalSessionRotationFailure(
  event: ApprovalSessionPowerEvent | "tray-background" | "tray-foreground",
  error: unknown
): void {
  if (approvalSessionRotationFailed) return;
  approvalSessionRotationFailed = true;
  console.error(`Approval session generation rotation failed during ${event}; shutting down safely.`, error);
  if (error instanceof ApprovalSessionGenerationInvalidationError) {
    // Both rename and truncate failed, so a separately managed backend could
    // still observe the old canonical file. Bound shutdown latency and force
    // this primary process down; service-managed backends remain an explicit
    // operational residual and must be stopped by their service boundary.
    const forcedExit = setTimeout(() => app.exit(1), 2_000);
    void backend.stop().finally(() => {
      clearTimeout(forcedExit);
      app.exit(1);
    });
    return;
  }
  void backend.stop().finally(() => app.quit());
}

function backendAutostartEnabled(): boolean {
  return process.env.LENGRVIS_BACKEND_AUTOSTART === "1";
}

function startHiddenRequested(): boolean {
  return process.argv.includes("--hidden") || app.getLoginItemSettings().wasOpenedAsHidden;
}
