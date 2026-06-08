const fs = require("node:fs");
const path = require("node:path");

const desktopRoot = path.resolve(__dirname, "..");
const distRoot = path.join(desktopRoot, "dist");
const generatedDirs = ["main", "preload", "shared"];

for (const dirName of generatedDirs) {
  const fullPath = path.join(distRoot, dirName);
  if (!fullPath.startsWith(distRoot + path.sep)) {
    throw new Error(`Refusing to clean path outside desktop dist: ${fullPath}`);
  }
  fs.rmSync(fullPath, { recursive: true, force: true });
}

