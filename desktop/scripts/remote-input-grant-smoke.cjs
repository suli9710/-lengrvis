const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const sourcePath = path.join(__dirname, "..", "src", "renderer", "lib", "remoteInputGrant.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    strict: true,
  },
}).outputText;

const sandbox = {
  exports: {},
  module: { exports: {} },
  require,
};
sandbox.exports = sandbox.module.exports;
vm.runInNewContext(compiled, sandbox, { filename: sourcePath });

const {
  activeRemoteInputGrantForDevice,
  isRemoteInputGrantActive,
  mobileDeviceCanReceiveRemoteInputGrant,
  remoteInputGrantExpiryTime,
} = sandbox.module.exports;

const now = Date.parse("2026-06-01T00:00:00.000Z");
const activeGrant = {
  id: "rig_active",
  status: "active",
  scope: "remote:input",
  created_at: "2026-06-01T00:00:00.000Z",
  expires_at: "2026-06-01T00:05:00.000Z",
};

assert.equal(remoteInputGrantExpiryTime(activeGrant), Date.parse(activeGrant.expires_at));
assert.equal(isRemoteInputGrantActive(activeGrant, now), true);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, expires_at: "" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, expires_at: "not-a-date" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, expires_at: "2026-05-31T23:59:59.000Z" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, status: "revoked", revoked_at: "2026-06-01T00:01:00.000Z" }, now), false);
assert.equal(mobileDeviceCanReceiveRemoteInputGrant({ device_id: "phone", device_name: "Phone", created_at: "", updated_at: "" }), true);
assert.equal(mobileDeviceCanReceiveRemoteInputGrant({ device_id: "phone", device_name: "Phone", created_at: "", updated_at: "" }, false), false);
assert.equal(
  mobileDeviceCanReceiveRemoteInputGrant({ device_id: "phone", device_name: "Phone", status: "revoked", created_at: "", updated_at: "" }),
  false,
);
assert.equal(
  activeRemoteInputGrantForDevice({
    device_id: "phone",
    device_name: "Phone",
    created_at: "",
    updated_at: "",
    remote_input_grants: [
      { ...activeGrant, id: "older", expires_at: "2026-06-01T00:03:00.000Z" },
      { ...activeGrant, id: "newer", expires_at: "2026-06-01T00:05:00.000Z" },
    ],
  }, now)?.id,
  "newer",
);

console.log("desktop remote input grant smoke passed");
