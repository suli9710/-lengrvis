const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const Module = require("node:module");
const path = require("node:path");

const helperPath = require.resolve(path.join(__dirname, "smoke-process-utils.cjs"));

class FakeChild extends EventEmitter {
  constructor(options = {}) {
    super();
    this.pid = options.pid ?? 4321;
    this.exitCode = options.exitCode ?? null;
    this.signalCode = options.signalCode ?? null;
    this.closeOnKill = options.closeOnKill ?? false;
    this.killedSignals = [];
    this.stdoutDestroyed = false;
    this.stderrDestroyed = false;
    this.stdout = { destroy: () => (this.stdoutDestroyed = true) };
    this.stderr = { destroy: () => (this.stderrDestroyed = true) };
  }

  kill(signal) {
    this.killedSignals.push(signal);
    if (this.closeOnKill && this.exitCode === null && this.signalCode === null) {
      this.signalCode = signal;
      setImmediate(() => this.emit("close", null, signal));
    }
    return true;
  }
}

function loadHelperWithSpawn(fakeSpawn) {
  const originalLoad = Module._load;
  delete require.cache[helperPath];
  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === "node:child_process") {
      return { spawn: fakeSpawn };
    }
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    return require(helperPath);
  } finally {
    Module._load = originalLoad;
    delete require.cache[helperPath];
  }
}

function fakeSpawnFrom(calls) {
  return (command, args, options) => {
    calls.push({ command, args, options });
    const proc = new EventEmitter();
    setImmediate(() => proc.emit("close", 0));
    return proc;
  };
}

async function alreadyClosedProcessDoesNothing() {
  const spawnCalls = [];
  const { stopProcessTree } = loadHelperWithSpawn(fakeSpawnFrom(spawnCalls));
  const child = new FakeChild({ exitCode: 0 });

  await stopProcessTree(child, { gracefulTimeoutMs: 1, forcedTimeoutMs: 1 });

  assert.deepEqual(child.killedSignals, []);
  assert.equal(child.stdoutDestroyed, false);
  assert.equal(child.stderrDestroyed, false);
  assert.deepEqual(spawnCalls, []);
}

async function gracefulCloseAvoidsForcedKill() {
  const spawnCalls = [];
  const { stopProcessTree } = loadHelperWithSpawn(fakeSpawnFrom(spawnCalls));
  const child = new FakeChild({ closeOnKill: true });

  await stopProcessTree(child, { gracefulTimeoutMs: 50, forcedTimeoutMs: 1 });

  assert.deepEqual(child.killedSignals, ["SIGTERM"]);
  assert.equal(child.stdoutDestroyed, false);
  assert.equal(child.stderrDestroyed, false);
  assert.deepEqual(spawnCalls, []);
}

async function timeoutForcesProcessTreeKillAndClosesStreams() {
  const spawnCalls = [];
  const { stopProcessTree } = loadHelperWithSpawn(fakeSpawnFrom(spawnCalls));
  const child = new FakeChild({ closeOnKill: false });

  await stopProcessTree(child, { gracefulTimeoutMs: 1, forcedTimeoutMs: 1 });

  assert.equal(child.killedSignals[0], "SIGTERM");
  assert.equal(child.stdoutDestroyed, true);
  assert.equal(child.stderrDestroyed, true);
  if (process.platform === "win32") {
    assert.deepEqual(spawnCalls, [
      {
        command: "taskkill",
        args: ["/PID", String(child.pid), "/T", "/F"],
        options: { stdio: "ignore" },
      },
    ]);
  } else {
    assert.deepEqual(child.killedSignals, ["SIGTERM", "SIGKILL"]);
    assert.deepEqual(spawnCalls, []);
  }
}

(async () => {
  await alreadyClosedProcessDoesNothing();
  await gracefulCloseAvoidsForcedKill();
  await timeoutForcesProcessTreeKillAndClosesStreams();
  console.log("smoke-process-utils passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
