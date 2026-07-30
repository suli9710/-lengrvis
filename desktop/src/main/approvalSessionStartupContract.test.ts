import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

describe("approval session startup contract", () => {
  it("initializes generation only after the process owns the single-instance lock", () => {
    const source = readFileSync(join(process.cwd(), "src", "main", "main.ts"), "utf-8");
    const lock = source.indexOf("const gotSingleInstanceLock = app.requestSingleInstanceLock()");
    const backendConstruction = source.indexOf("const backend = new BackendProcessManager()");
    const deniedBranch = source.indexOf("if (!gotSingleInstanceLock)");
    const primaryInitialization = source.indexOf("backend.initializeApprovalSessionGeneration()", deniedBranch);

    expect(lock).toBeGreaterThanOrEqual(0);
    expect(backendConstruction).toBeGreaterThan(lock);
    expect(deniedBranch).toBeGreaterThan(backendConstruction);
    expect(primaryInitialization).toBeGreaterThan(deniedBranch);
    expect(source.slice(deniedBranch, primaryInitialization)).toContain("app.quit()");
    expect(source.slice(deniedBranch, primaryInitialization)).toContain("} else {");
  });

  it("revokes through the visibility coordinator before hiding to the tray", () => {
    const source = readFileSync(join(process.cwd(), "src", "main", "main.ts"), "utf-8");
    const backgroundFunction = source.indexOf("async function enterTrayBackground()");
    const backgroundBinding = source.indexOf("approvalSessionVisibility.enterBackground", backgroundFunction);
    const hide = source.indexOf("mainWindow?.hide()", backgroundFunction);
    const foregroundFunction = source.indexOf("async function enterForegroundAndShow()");
    const foregroundBinding = source.indexOf("approvalSessionVisibility.enterForeground", foregroundFunction);
    const show = source.indexOf("mainWindow.show()", foregroundFunction);

    expect(backgroundFunction).toBeGreaterThanOrEqual(0);
    expect(backgroundBinding).toBeGreaterThan(backgroundFunction);
    expect(hide).toBeGreaterThan(backgroundBinding);
    expect(foregroundBinding).toBeGreaterThan(foregroundFunction);
    expect(show).toBeGreaterThan(foregroundBinding);
  });

  it("does not let a stale ready-to-show event reopen a background window", () => {
    const source = readFileSync(join(process.cwd(), "src", "main", "main.ts"), "utf-8");
    const readyToShow = source.indexOf('window.once("ready-to-show"');
    const visibilityGuard = source.indexOf(
      "approvalSessionVisibility.isForegroundRequested()",
      readyToShow
    );
    const show = source.indexOf("window.show()", readyToShow);

    expect(readyToShow).toBeGreaterThanOrEqual(0);
    expect(visibilityGuard).toBeGreaterThan(readyToShow);
    expect(show).toBeGreaterThan(visibilityGuard);
  });

  it("revokes signing synchronously for hidden login startup", () => {
    const source = readFileSync(join(process.cwd(), "src", "main", "main.ts"), "utf-8");
    const initialization = source.indexOf("backend.initializeApprovalSessionGeneration()");
    const hiddenCheck = source.indexOf("if (startHiddenRequested())", initialization);
    const deactivation = source.indexOf(
      "backend.deactivateApprovalSessionGeneration()",
      hiddenCheck
    );
    const ready = source.indexOf("app.whenReady()", initialization);

    expect(initialization).toBeGreaterThanOrEqual(0);
    expect(hiddenCheck).toBeGreaterThan(initialization);
    expect(deactivation).toBeGreaterThan(hiddenCheck);
    expect(deactivation).toBeLessThan(ready);
  });

  it("registers and loads the packaged renderer through the constrained app protocol", () => {
    const source = readFileSync(join(process.cwd(), "src", "main", "main.ts"), "utf-8");
    const privilegeRegistration = source.indexOf("registerRendererSchemePrivileges()");
    const ready = source.indexOf("app.whenReady()");
    const handlerRegistration = source.indexOf("registerPackagedRendererProtocol(", ready);
    const windowCreation = source.indexOf("mainWindow = createMainWindow()", ready);
    const packagedLoad = source.indexOf("window.loadURL(PACKAGED_RENDERER_ENTRY_URL)");

    expect(privilegeRegistration).toBeGreaterThanOrEqual(0);
    expect(privilegeRegistration).toBeLessThan(ready);
    expect(handlerRegistration).toBeGreaterThan(ready);
    expect(handlerRegistration).toBeLessThan(windowCreation);
    expect(packagedLoad).toBeGreaterThanOrEqual(0);
    expect(source).not.toContain("window.loadFile(");
  });
});
