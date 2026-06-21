/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Electron main process entry — consent gate integration.
 *
 * This file shows the integration points where registerConsentIpcHandlers
 * should be called and where the consent check should happen before
 * showing the main window.
 *
 * The changes are minimal: import + register + check.
 */

// === ADDED IMPORT (top of main.ts, alongside existing imports) ===
import { registerConsentIpcHandlers } from "./consentManager";

// === ADDED INSIDE app.whenReady().then(async () => { ... }) ===
// After: registerIpcHandlers(backend);
//        registerDesktopWebSocketIpcHandlers(backend);
// Add:
//   registerConsentIpcHandlers();
//
// Full context (illustration — do not duplicate existing code):
//
// app.whenReady().then(async () => {
//   Menu.setApplicationMenu(null);
//   registerIpcHandlers(backend);
//   registerDesktopWebSocketIpcHandlers(backend);
//   registerConsentIpcHandlers();   // <-- NEW LINE
//   browserHost.registerIpcHandlers();
//   notifications.registerIpcHandlers();
//   mainWindow = createMainWindow();
//   ... (rest of existing code)
// });
