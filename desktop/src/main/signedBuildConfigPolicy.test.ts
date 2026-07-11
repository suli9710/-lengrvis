import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";

import { describe, expect, it } from "vitest";

const desktopRoot = process.cwd();
const macEntitlementsPath = resolve(desktopRoot, "build", "entitlements.mac.plist");
const signedBuildVerifierPath = resolve(desktopRoot, "scripts", "verify-signed-build-config.cjs");

describe("signed macOS build policy", () => {
  it("keeps Hardened Runtime library validation enabled", () => {
    const entitlements = readFileSync(macEntitlementsPath, "utf8");

    expect(entitlements).not.toContain("com.apple.security.cs.disable-library-validation");
  });

  it("makes the signed-build verifier reject the forbidden entitlement", () => {
    const tempDesktopRoot = mkdtempSync(resolve(tmpdir(), "lengrvis-signed-build-policy-"));
    try {
      const scriptsDir = resolve(tempDesktopRoot, "scripts");
      const buildDir = resolve(tempDesktopRoot, "build");
      mkdirSync(scriptsDir);
      mkdirSync(buildDir);
      writeFileSync(resolve(scriptsDir, "verify-signed-build-config.cjs"), readFileSync(signedBuildVerifierPath));
      writeFileSync(
        resolve(tempDesktopRoot, "electron-builder.signed.js"),
        "hardenedRuntime: true\\ngatekeeperAssess: false\\nentitlements.mac.plist\\nnotarize: macNotarizeOptions()\\n"
      );
      writeFileSync(
        resolve(buildDir, "entitlements.mac.plist"),
        "<key>com.apple.security.cs.disable-library-validation</key>"
      );

      const result = spawnSync(process.execPath, [resolve(scriptsDir, "verify-signed-build-config.cjs"), "--structure-only", "mac"], {
        encoding: "utf8"
      });

      expect(result.status).toBe(1);
      expect(result.stderr).toContain("macOS entitlements must not enable com.apple.security.cs.disable-library-validation");
    } finally {
      rmSync(tempDesktopRoot, { force: true, recursive: true });
    }
  });
});
