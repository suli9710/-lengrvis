const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const sourcePath = path.join(__dirname, "..", "src", "renderer", "lib", "documentScope.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    strict: true
  }
}).outputText;

const sandbox = {
  exports: {},
  module: { exports: {} },
  require
};
sandbox.exports = sandbox.module.exports;
vm.runInNewContext(compiled, sandbox, { filename: sourcePath });

const { normalizePathForCompare, parentDirectory, isPathWithinScope } = sandbox.module.exports;

assert.equal(normalizePathForCompare("C:\\Users\\Suli\\Documents\\"), "c:/users/suli/documents");
assert.equal(parentDirectory("C:\\Users\\Suli\\Documents\\report.pdf"), "C:\\Users\\Suli\\Documents");
assert.equal(parentDirectory("C:\\report.pdf"), "C:\\");
assert.equal(parentDirectory("\\\\server\\share\\report.pdf"), "\\\\server\\share");
assert.equal(parentDirectory("/Users/suli/Documents/report.pdf"), "/Users/suli/Documents");
assert.equal(parentDirectory("report.pdf"), "");

assert.equal(isPathWithinScope("C:\\Users\\Suli\\Documents\\report.pdf", ["C:\\Users\\Suli\\Documents"]), true);
assert.equal(isPathWithinScope("C:\\Users\\Suli\\Documents2\\report.pdf", ["C:\\Users\\Suli\\Documents"]), false);
assert.equal(isPathWithinScope("/Users/suli/Documents/report.pdf", ["/Users/suli/Documents"]), true);
assert.equal(isPathWithinScope("/Users/suli/Downloads/report.pdf", ["/Users/suli/Documents"]), false);
assert.equal(isPathWithinScope("\\\\server\\share\\report.pdf", ["\\\\server\\share"]), true);

console.log("document-scope smoke passed");
