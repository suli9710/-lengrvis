const { existsSync, statSync } = require("node:fs");
const { join } = require("node:path");

const platformAliases = new Map([
  ["win", "win32"],
  ["windows", "win32"],
  ["win32", "win32"],
  ["mac", "darwin"],
  ["macos", "darwin"],
  ["darwin", "darwin"],
  ["linux", "linux"]
]);

const requestedPlatform = process.argv[2] ? process.argv[2].toLowerCase() : process.platform;
const platform = platformAliases.get(requestedPlatform);

if (!platform) {
  console.error(`Unsupported backend resource platform: ${requestedPlatform}`);
  process.exit(2);
}

const binaryName = platform === "win32" ? "backend.exe" : "backend";
const binaryPath = join(__dirname, "..", "..", "dist", binaryName);

if (!existsSync(binaryPath)) {
  console.error(`Missing backend binary for ${platform}: ${binaryPath}`);
  console.error(
    platform === "darwin"
      ? "Build it on macOS with: bash scripts/build_backend_mac.sh arm64"
      : "Build it first with the platform backend build script."
  );
  process.exit(1);
}

const stats = statSync(binaryPath);
if (!stats.isFile() || stats.size === 0) {
  console.error(`Invalid backend binary for ${platform}: ${binaryPath}`);
  process.exit(1);
}

console.log(`Backend binary ready for ${platform}: ${binaryPath}`);
