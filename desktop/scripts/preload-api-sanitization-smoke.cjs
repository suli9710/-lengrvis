const assert = require("node:assert/strict");
const Module = require("node:module");

const originalLoad = Module._load;
const exposed = {};
const invocations = [];

Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "electron") {
    return {
      contextBridge: {
        exposeInMainWorld: (name, value) => {
          exposed[name] = value;
        }
      },
      ipcRenderer: {
        invoke: async (channel, ...args) => {
          invocations.push({ channel, args });
          return { ok: true, status: 200, receivedAt: "2026-06-08T00:00:00.000Z" };
        },
        on: () => undefined,
        removeListener: () => undefined
      }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

function latestRequest() {
  assert.ok(invocations.length > 0, "expected at least one IPC invocation");
  return invocations[invocations.length - 1].args[0];
}

async function assertRejectsWithoutInvoke(request, pattern, label) {
  const before = invocations.length;
  await assert.rejects(() => exposed.lengrvis.api.request(request), pattern, label);
  assert.equal(invocations.length, before, `${label} must not reach IPC`);
}

(async () => {
  try {
    require("../dist/preload/preload.js");

    assert.ok(exposed.lengrvis, "preload should expose the Lengrvis bridge");
    assert.equal(typeof exposed.lengrvis.api.request, "function", "api.request should be exposed");

    await exposed.lengrvis.api.request({
      endpoint: "/api/chat",
      method: "POST",
      query: {
        q: "hello",
        page: 1,
        active: true,
        none: null,
        unset: undefined
      },
      body: {
        message: "hello",
        tags: ["alpha", "beta"],
        nested: { count: 1, enabled: true, empty: null }
      },
      timeoutMs: 1000
    });

    assert.equal(invocations[0].channel, "lengrvis:api:request");
    assert.deepEqual(latestRequest(), {
      endpoint: "/api/chat",
      method: "POST",
      query: {
        q: "hello",
        page: 1,
        active: true,
        none: null,
        unset: undefined
      },
      body: {
        message: "hello",
        tags: ["alpha", "beta"],
        nested: { count: 1, enabled: true, empty: null }
      },
      timeoutMs: 1000
    });

    const nullPrototypeBody = Object.create(null);
    nullPrototypeBody.message = "plain clone";
    nullPrototypeBody.tags = ["safe"];
    await exposed.lengrvis.api.request({
      endpoint: "/api/chat",
      method: "POST",
      body: nullPrototypeBody
    });
    assert.equal(Object.getPrototypeOf(latestRequest().body), Object.prototype);
    assert.deepEqual(latestRequest().body, { message: "plain clone", tags: ["safe"] });

    await assertRejectsWithoutInvoke(() => undefined, /plain object/, "function request");
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/health", headers: { Authorization: "Bearer leaked" } },
      /field is not allowed/,
      "custom headers"
    );
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/health", query: { callback: () => "x" } },
      /query values must be primitive/,
      "function query value"
    );
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/health", query: { token: Symbol("secret") } },
      /query values must be primitive/,
      "symbol query value"
    );
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: { callback: () => "x" } },
      /plain JSON data/,
      "function body value"
    );
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: { token: Symbol("secret") } },
      /plain JSON data/,
      "symbol body value"
    );
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: { bytes: new ArrayBuffer(8) } },
      /plain object/,
      "ArrayBuffer body value"
    );

    if (typeof Blob === "function") {
      await assertRejectsWithoutInvoke(
        { endpoint: "/api/chat", method: "POST", body: { file: new Blob(["x"]) } },
        /plain object/,
        "Blob body value"
      );
    }
    if (typeof File === "function") {
      await assertRejectsWithoutInvoke(
        { endpoint: "/api/chat", method: "POST", body: { file: new File(["x"], "x.txt") } },
        /plain object/,
        "File body value"
      );
    }

    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: JSON.parse('{"__proto__":{"polluted":true}}') },
      /key is invalid/,
      "prototype pollution key"
    );
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: { constructor: { prototype: { polluted: true } } } },
      /key is invalid/,
      "constructor prototype pollution key"
    );

    class Payload {}
    const classPayload = new Payload();
    classPayload.message = "not plain";
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: classPayload },
      /plain object/,
      "class instance body"
    );

    let getterTouched = false;
    const accessorRequest = {};
    Object.defineProperty(accessorRequest, "endpoint", {
      enumerable: true,
      get() {
        getterTouched = true;
        return "/api/health";
      }
    });
    await assertRejectsWithoutInvoke(accessorRequest, /accessor fields/, "request accessor");
    assert.equal(getterTouched, false, "request getter must not be invoked");

    const accessorBody = {};
    Object.defineProperty(accessorBody, "secret", {
      enumerable: true,
      get() {
        getterTouched = true;
        return "leaked";
      }
    });
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: accessorBody },
      /accessor fields/,
      "body accessor"
    );
    assert.equal(getterTouched, false, "body getter must not be invoked");

    const symbolKeyBody = { message: "safe" };
    symbolKeyBody[Symbol("secret")] = "leaked";
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: symbolKeyBody },
      /symbol keys/,
      "symbol body key"
    );

    const arrayWithExtraField = ["safe"];
    arrayWithExtraField.extra = "not json";
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: { items: arrayWithExtraField } },
      /array must not contain object fields/,
      "array object field"
    );

    const sparseArray = [];
    sparseArray[1] = "hole";
    await assertRejectsWithoutInvoke(
      { endpoint: "/api/chat", method: "POST", body: { items: sparseArray } },
      /array must not be sparse/,
      "sparse array"
    );

    assert.equal({}.polluted, undefined, "prototype pollution payload must not affect Object.prototype");
    console.log("preload API sanitization smoke passed");
  } finally {
    Module._load = originalLoad;
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
