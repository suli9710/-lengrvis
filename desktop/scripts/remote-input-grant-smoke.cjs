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
  device_id: "phone",
  status: "active",
  scope: "remote:input",
  created_at: "2026-06-01T00:00:00.000Z",
  expires_at: "2026-06-01T00:05:00.000Z",
};
const activeGrantWithoutDeviceId = { ...activeGrant };
delete activeGrantWithoutDeviceId.device_id;

assert.equal(remoteInputGrantExpiryTime(activeGrant), Date.parse(activeGrant.expires_at));
assert.equal(isRemoteInputGrantActive(activeGrant, now), true);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, status: undefined }, now), true);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, id: "" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, device_id: "" }, now), false);
assert.equal(isRemoteInputGrantActive(activeGrantWithoutDeviceId, now), false);
assert.equal(isRemoteInputGrantActive(activeGrantWithoutDeviceId, now, "phone"), true);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, device_id: "other-phone" }, now, "phone"), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, expires_at: "" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, expires_at: "not-a-date" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, expires_at: "2026-05-31T23:59:59.000Z" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, status: "revoked", revoked_at: "2026-06-01T00:01:00.000Z" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, revoked_at: "2026-06-01T00:01:00.000Z" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, status: "pending" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, scope: "remote:view" }, now), false);
assert.equal(isRemoteInputGrantActive({ ...activeGrant, scope: "" }, now), false);
assert.equal(mobileDeviceCanReceiveRemoteInputGrant({ device_id: "phone", device_name: "Phone", created_at: "", updated_at: "" }), true);
assert.equal(mobileDeviceCanReceiveRemoteInputGrant({ device_id: "phone", device_name: "Phone", created_at: "", updated_at: "" }, false), false);
assert.equal(mobileDeviceCanReceiveRemoteInputGrant({ device_id: "", device_name: "Phone", created_at: "", updated_at: "" }), false);
assert.equal(
  mobileDeviceCanReceiveRemoteInputGrant({
    device_id: "phone",
    device_name: "Phone",
    revoked_at: "2026-06-01T00:01:00.000Z",
    created_at: "",
    updated_at: "",
  }),
  false,
);
assert.equal(mobileDeviceCanReceiveRemoteInputGrant({ device_id: "phone", device_name: "Phone", status: "pending", created_at: "", updated_at: "" }), false);
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
      { ...activeGrant, id: "other_device", device_id: "other-phone", expires_at: "2026-06-01T00:12:00.000Z" },
      { ...activeGrant, id: "view_only", scope: "remote:view", expires_at: "2026-06-01T00:10:00.000Z" },
      { ...activeGrant, id: "", expires_at: "2026-06-01T00:09:00.000Z" },
      { ...activeGrant, id: "older", expires_at: "2026-06-01T00:03:00.000Z" },
      { ...activeGrant, id: "newer", expires_at: "2026-06-01T00:05:00.000Z" },
    ],
  }, now)?.id,
  "newer",
);
assert.equal(
  activeRemoteInputGrantForDevice({
    device_id: "phone",
    device_name: "Phone",
    created_at: "",
    updated_at: "",
    remote_input_grants: [{ ...activeGrant, id: "view_only", scope: "remote:view" }],
  }, now),
  null,
);
assert.equal(
  activeRemoteInputGrantForDevice({
    device_id: "phone",
    device_name: "Phone",
    created_at: "",
    updated_at: "",
    remote_input_grants: [{ ...activeGrantWithoutDeviceId, id: "nested_backend_grant" }],
  }, now)?.id,
  "nested_backend_grant",
);

assert.equal(
  activeRemoteInputGrantForDevice({
    device_id: "phone",
    device_name: "Phone",
    status: "pending",
    created_at: "",
    updated_at: "",
    remote_input_grants: [{ ...activeGrant, id: "would_be_active" }],
  }, now),
  null,
);
assert.equal(
  activeRemoteInputGrantForDevice({
    device_id: "phone",
    device_name: "Phone",
    status: "revoked",
    created_at: "",
    updated_at: "",
    remote_input_grants: [{ ...activeGrant, id: "would_be_active" }],
  }, now),
  null,
);

console.log("desktop remote input grant smoke passed");
