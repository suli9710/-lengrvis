const fs = require("node:fs");
const { createHash } = require("node:crypto");

// SDK installs may be reached through Windows junctions/8.3 aliases. Resolve
// filesystem identity, not merely lexical GetFullPath, before granting a match.
function verifyNativeToolIdentity(expected, selected, io = {}) {
  const realpath = io.realpath ?? fs.realpathSync.native;
  const stat = io.stat ?? fs.statSync;
  const readFile = io.readFile ?? fs.readFileSync;
  const platform = io.platform ?? process.platform;
  const expectedPath = realpath(expected);
  const selectedPath = realpath(selected);
  const key = (value) => platform === "win32" ? value.toLowerCase() : value;
  if (key(expectedPath) !== key(selectedPath)) {
    throw new Error(`Unexpected native generator ${selectedPath}; expected ${expectedPath}`);
  }
  const expectedInfo = stat(expectedPath, { bigint: true });
  const selectedInfo = stat(selectedPath, { bigint: true });
  if (!expectedInfo.isFile() || !selectedInfo.isFile()) {
    throw new Error("Native generator must be a regular file");
  }
  if (expectedInfo.dev !== selectedInfo.dev || expectedInfo.ino !== selectedInfo.ino) {
    throw new Error("Native generator file identity differs");
  }
  const hash = (file) => createHash("sha256").update(readFile(file)).digest("hex");
  const expectedHash = hash(expectedPath);
  if (expectedHash !== hash(selectedPath)) {
    throw new Error("Native generator content changed during identity verification");
  }
  return { canonicalPath: expectedPath, sha256: expectedHash };
}

if (require.main === module) {
  try {
    if (process.argv.length !== 4) throw new Error("Expected verified SDK tool path and CMake-selected tool path");
    console.log(JSON.stringify(verifyNativeToolIdentity(process.argv[2], process.argv[3])));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
module.exports = { verifyNativeToolIdentity };
