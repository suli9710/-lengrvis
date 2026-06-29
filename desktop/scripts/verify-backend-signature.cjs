// Release gate: signed distribution builds must not package an unsigned
// backend binary. The release process signs dist/backend.exe (signtool /
// Azure Trusted Signing) BEFORE electron-builder copies it in as an
// extraResource; this script makes that ordering an executable check
// instead of a comment-only convention.
const { existsSync } = require("node:fs");
const { join } = require("node:path");
const { execFileSync } = require("node:child_process");

const platformAliases = new Map([
  ["win", "win32"],
  ["windows", "win32"],
  ["win32", "win32"],
  ["mac", "darwin"],
  ["macos", "darwin"],
  ["darwin", "darwin"]
]);

const requestedPlatform = process.argv[2] ? process.argv[2].toLowerCase() : process.platform;
const platform = platformAliases.get(requestedPlatform);

if (!platform) {
  console.error(`Unsupported backend signature platform: ${requestedPlatform}`);
  process.exit(2);
}

const binaryName = platform === "win32" ? "backend.exe" : "backend";
const binaryPath = join(__dirname, "..", "..", "dist", binaryName);

if (!existsSync(binaryPath)) {
  console.error(`Missing backend binary: ${binaryPath}`);
  console.error("Build the backend first; the signed distribution pipeline signs it before packaging.");
  process.exit(1);
}

function fail(detail) {
  console.error(`Backend binary is not validly signed: ${binaryPath}`);
  if (detail) {
    console.error(detail);
  }
  console.error(
    platform === "win32"
      ? "Sign it before packaging, e.g.: signtool sign /tr http://timestamp.acs.microsoft.com /td SHA256 /fd SHA256 ... dist/backend.exe (or the Azure Trusted Signing CLI)."
      : "Sign it before packaging with: codesign --sign <identity> --timestamp --options runtime dist/backend"
  );
  console.error("Unsigned local builds should use `npm run dist:unsigned` instead of dist:signed/dist:publish.");
  process.exit(1);
}

try {
  if (platform === "win32") {
    const status = execFileSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        `(Get-AuthenticodeSignature -LiteralPath '${binaryPath.replace(/'/g, "''")}').Status.ToString()`
      ],
      { encoding: "utf8" }
    ).trim();
    if (status !== "Valid") {
      fail(`Authenticode status: ${status || "(empty)"}`);
    }
  } else {
    execFileSync("codesign", ["--verify", "--strict", binaryPath], { stdio: "pipe" });
  }
} catch (error) {
  fail(String(error && error.message ? error.message : error));
}

console.log(`Backend binary signature verified for ${platform}: ${binaryPath}`);
