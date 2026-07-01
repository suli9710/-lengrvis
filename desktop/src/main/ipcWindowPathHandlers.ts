import { app, BrowserWindow, dialog, ipcMain, type OpenDialogOptions } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import { openSafeExternalUrl } from "./externalUrl";
import type { IpcPathGrantStores } from "./ipcPathGrants";
import {
  getFileIconDataUrl,
  rememberDocumentPathGrant,
  showItemInFolder
} from "./ipcPathGrants";
import { validateBridgePathValue } from "./ipcValidation";
import { assertTrustedRenderer } from "./rendererTrust";

export function registerWindowPathIpcHandlers(grants: IpcPathGrantStores): void {
  const { documentPathGrants } = grants;

  ipcMain.handle(IPC_CHANNELS.openExternal, async (event, url: string) => {
    assertTrustedRenderer(event);
    await openSafeExternalUrl(url);
  });

  ipcMain.handle(IPC_CHANNELS.getFileIcon, async (event, filePath: string) => {
    assertTrustedRenderer(event);
    return getFileIconDataUrl(filePath, grants);
  });

  ipcMain.handle(IPC_CHANNELS.showItemInFolder, async (event, filePath: unknown) => {
    assertTrustedRenderer(event);
    return showItemInFolder(validateBridgePathValue(filePath, "file path to reveal"), grants);
  });

  ipcMain.handle(IPC_CHANNELS.chooseDirectory, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "选择文件夹",
      properties: ["openDirectory", "createDirectory"]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.chooseDocument, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "选择文档",
      properties: ["openFile"],
      filters: [
        {
          name: "可读取文档",
          extensions: [
            "pdf",
            "docx",
            "txt",
            "md",
            "markdown",
            "log",
            "rst",
            "json",
            "yaml",
            "yml",
            "py",
            "ts",
            "tsx",
            "js",
            "csv",
            "xlsx",
            "pptx",
            "html",
            "htm",
            "png",
            "jpg",
            "jpeg",
            "webp",
            "bmp",
            "tif",
            "tiff"
          ]
        },
        { name: "所有文件", extensions: ["*"] }
      ]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    const picked = result.canceled ? null : result.filePaths[0] ?? null;
    if (picked) {
      rememberDocumentPathGrant(documentPathGrants, picked);
    }
    return picked;
  });

  ipcMain.handle(IPC_CHANNELS.knownFolders, (event) => {
    assertTrustedRenderer(event);
    return {
      desktop: app.getPath("desktop"),
      downloads: app.getPath("downloads"),
      documents: app.getPath("documents"),
      pictures: app.getPath("pictures")
    };
  });

  ipcMain.handle(IPC_CHANNELS.chooseSkillDirectory, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Select skill package directory",
      properties: ["openDirectory"]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.chooseSkillZip, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Select skill zip package",
      properties: ["openFile"],
      filters: [{ name: "Skill packages", extensions: ["zip"] }]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });
}
