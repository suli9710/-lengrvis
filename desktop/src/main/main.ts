import { app, BrowserWindow, Menu, shell } from "electron";
import { existsSync } from "node:fs";
import { join } from "node:path";

import { BackendProcessManager } from "./backendProcess";
import { registerIpcHandlers } from "./ipc";

const isDev = Boolean(process.env.VITE_DEV_SERVER_URL);
const backend = new BackendProcessManager();

function getPackagedBackendName(): string {
  return process.platform === "win32" ? "backend.exe" : "backend";
}

function isPortableMode(): boolean {
  return existsSync(join(process.resourcesPath, "backend", getPackagedBackendName()));
}

let mainWindow: BrowserWindow | null = null;

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
    shell.openExternal(url);
    return { action: "deny" };
  });

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    window.loadURL(process.env.VITE_DEV_SERVER_URL);
    window.webContents.openDevTools({ mode: "detach" });
  } else {
    window.loadFile(join(__dirname, "../renderer/index.html"));
  }

  return window;
}

app.setName("Mavris");

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  registerIpcHandlers(backend);
  mainWindow = createMainWindow();

  if (!process.defaultApp || app.isPackaged || isPortableMode() || process.env.MAVRIS_BACKEND_AUTOSTART === "1") {
    await backend.start();
  }

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
  await backend.stop();
});
