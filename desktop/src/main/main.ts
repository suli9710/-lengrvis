import { app, BrowserWindow, Menu, Tray, nativeImage, shell, type MenuItemConstructorOptions } from "electron";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import type { BackendStatus } from "../shared/types";
import { BackendProcessManager } from "./backendProcess";
import { isSafeExternalUrl, registerIpcHandlers } from "./ipc";
import { NotificationBridge } from "./notifications";

const isDev = Boolean(process.env.VITE_DEV_SERVER_URL);
const BACKEND_STATUS_POLL_MS = 10_000;
const TRAY_ICON_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAI/SURBVFhH1VcxTwIxGGV0dHTDxFZXR0d/ghtuaMvg6OiG8Q84urEwODrq5kLiaJgcSVg4rgTCQIhhqPlKr7RfW+7OHCa+5EWOe/R7fe199Wq1/wrSSs6OuDi3iTWVot4cHxCW3FAmXikXMkomPsj1+O6kOTrEY/wK9eZsn3JxT7lYesVyOXkC43jMwoBYKU9n/sCluKQsvcRj54Ky8dXvZh3lPa4RhS6OB6iCj7iWh3Xslc7cIWxkXNMANkwFa57HJW2NT3FtBYgo8APZ7ksHw7epp6EPCzm0RclCNrBGk/DJC66tZx+OHhsIDd54W+VqHOIUoHl4ImwgWelZrmT3wdZMZTdZfz9UfwsY4JMnxwB0MF8UM4CWwcT/LXtGm2dAjExx3e2wIGBgIbuBAib+/tzR5hjYLAMcLN7NiIFG51tfZMuQxS9lr7P5XMhA1iGPWXLh3YwZ4HPZ05dqGaz425aZIgbIdXq7TgBOuYAgbMC9blvx22kUMWDaM0QRuBk1QM0ybDZdrwPacgbgyVMGdPv1BFEDdiEFiL+8AThz9FOgmpAviBpAjUfFX94AbH79INZqhIsBFmwzYLfedfylDSzrzcGeMRA7B3ZF7zyApoBFuyQ8+o4BALjCwl2QMPGJayv8VQrB2WfYdipWQ3QKhkC4ePZ/WAGZ+HB2fgwgqtyEKj7bx7W2Qr+Q+IOVJrygFJh5CKpNs/TdHzSfhKdfWzdcGcBAelmC/zc6hPfHrM/vApCKflpgiQzh+7JR/wBFmasNoNL4MAAAAABJRU5ErkJggg==";
const backend = new BackendProcessManager();
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

function createMainWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1120,
    minHeight: 720,
    title: "Mavris",
    backgroundColor: "#f4f6f8",
    show: false,
    webPreferences: {
      preload: join(__dirname, "../preload/preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });

  window.once("ready-to-show", () => {
    window.show();
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalUrl(url)) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (isDev && url === process.env.VITE_DEV_SERVER_URL) {
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

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    window.loadURL(process.env.VITE_DEV_SERVER_URL);
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
  tray.setToolTip("Mavris");
  tray.on("click", showMainWindow);
  rebuildTrayMenu();
}

function showMainWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    mainWindow = createMainWindow();
  }

  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
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
      label: "打开 Mavris",
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
      label: "退出",
      click: () => {
        app.quit();
      }
    }
  ];

  tray.setToolTip(`Mavris - ${statusText}`);
  tray.setContextMenu(Menu.buildFromTemplate(template));
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

app.setName("Mavris");
if (process.platform === "win32") {
  app.setAppUserModelId("Mavris");
}

const gotSingleInstanceLock = app.requestSingleInstanceLock();

if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    showMainWindow();
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    registerIpcHandlers(backend);
    notifications.registerIpcHandlers();
    mainWindow = createMainWindow();
    createTray();
    notifications.startBackendListener();

    if (!process.defaultApp || app.isPackaged || isPortableMode() || process.env.MAVRIS_BACKEND_AUTOSTART === "1") {
      latestBackendStatus = await backend.start();
      rebuildTrayMenu();
    }
    startTrayBackendStatusPolling();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        mainWindow = createMainWindow();
      }
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
      app.quit();
    }
  });

  app.on("before-quit", async () => {
    stopTrayBackendStatusPolling();
    notifications.stopBackendListener();
    await backend.stop();
  });
}
