const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const mobileRoot = path.resolve(__dirname, "..");
const formatSource = fs.readFileSync(path.join(mobileRoot, "src", "format.ts"), "utf8");
const typesSource = fs.readFileSync(path.join(mobileRoot, "src", "api", "client", "types.ts"), "utf8");

assert.match(
  typesSource,
  /export type BackendApprovalStatus = [^;]*\(string & \{\}\)/,
  "BackendApproval status type should allow unknown backend statuses",
);
assert.match(formatSource, /if \(status === "pending"\) return "待审批";/);
assert.match(formatSource, /return "状态未知";/);
assert.ok(
  formatSource.indexOf('if (status === "pending") return "待审批";') < formatSource.indexOf('return "状态未知";'),
  "unknown statuses must not fall through to the pending label",
);

console.log("[pass] Approval status labels fail closed for unknown statuses");
