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

const { normalizePathForCompare, parentDirectory, isPathWithinScope, uniqueScopePaths, mergeScopePaths, documentScopesForFiles } = sandbox.module.exports;

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

assert.deepEqual([...uniqueScopePaths(["C:\\Users\\Suli\\Documents", "c:/users/suli/documents/", "D:\\Work"])], [
  "C:\\Users\\Suli\\Documents",
  "D:\\Work"
]);
assert.deepEqual([...mergeScopePaths(["C:\\A\\B", "C:\\A"])], ["C:\\A"]);
assert.deepEqual([...mergeScopePaths(["C:\\A", "C:\\A\\B"])], ["C:\\A"]);
assert.deepEqual([...documentScopesForFiles([
  "C:\\Users\\Suli\\Documents\\one.pdf",
  "D:\\Work\\two.pdf"
], [])], [
  "C:\\Users\\Suli\\Documents",
  "D:\\Work"
]);
assert.deepEqual([...documentScopesForFiles([
  "C:\\Users\\Suli\\Documents\\one.pdf",
  "C:\\Users\\Suli\\Documents\\nested\\two.pdf"
], ["C:\\Users\\Suli\\Documents"])], []);
assert.deepEqual([...documentScopesForFiles(["C:\\Users\\Suli\\Documents2\\report.pdf"], ["C:\\Users\\Suli\\Documents"])], [
  "C:\\Users\\Suli\\Documents2"
]);
assert.deepEqual([...documentScopesForFiles([
  "C:\\A\\B\\one.pdf",
  "C:\\A\\two.pdf"
], [])], ["C:\\A"]);

const workspaceSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "renderer", "components", "file-search", "FileDocumentWorkspace.tsx"),
  "utf8"
);
const compareStart = workspaceSource.indexOf("const compareDocuments = useCallback(async () =>");
const ensureBeforeCompare = workspaceSource.indexOf(
  "ensureDocumentScopes([selectedDocumentPathValue, compareDocumentPathValue])",
  compareStart
);
const compareCall = workspaceSource.indexOf("api.compareDocuments", compareStart);
assert.ok(compareStart >= 0, "compareDocuments handler should exist");
assert.ok(ensureBeforeCompare > compareStart, "compareDocuments should ensure both document scopes");
assert.ok(compareCall > ensureBeforeCompare, "compareDocuments should save scopes before calling the backend compare API");
assert.match(workspaceSource, /const ensureDocumentScope = useCallback\([\s\S]*ensureDocumentScopes\(\[filePath\]\)/);

console.log("document-scope smoke passed");
