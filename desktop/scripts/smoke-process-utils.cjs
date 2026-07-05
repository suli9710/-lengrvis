const { spawn } = require("node:child_process");

async function stopProcessTree(child, options = {}) {
  if (!child) return;
  const gracefulTimeoutMs = options.gracefulTimeoutMs ?? 3_000;
  const forcedTimeoutMs = options.forcedTimeoutMs ?? 3_000;
  const closed = waitForClose(child);

  if (!isClosed(child)) {
    try {
      child.kill("SIGTERM");
    } catch {
      // The process may have already exited between checks.
    }
  }
  if (await withTimeout(closed, gracefulTimeoutMs)) return;
  if (isClosed(child)) {
    child.stdout?.destroy();
    child.stderr?.destroy();
    return;
  }

  await forceKillProcessTree(child);
  await withTimeout(closed, forcedTimeoutMs);
  child.stdout?.destroy();
  child.stderr?.destroy();
}

function isClosed(child) {
  return child.exitCode !== null || child.signalCode !== null;
}

function waitForClose(child) {
  if (isClosed(child)) return Promise.resolve(true);
  return new Promise((resolve) => {
    child.once("close", () => resolve(true));
  });
}

async function forceKillProcessTree(child) {
  if (!child.pid) return;
  if (process.platform === "win32") {
    await runProcess("taskkill", ["/PID", String(child.pid), "/T", "/F"]);
    return;
  }
  try {
    child.kill("SIGKILL");
  } catch {
    // Best effort; callers still wait for the close timeout.
  }
}

function runProcess(command, args) {
  return new Promise((resolve) => {
    const proc = spawn(command, args, { stdio: "ignore" });
    proc.once("error", () => resolve(false));
    proc.once("close", (code) => resolve(code === 0));
  });
}

function withTimeout(promise, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    promise.then(
      () => {
        clearTimeout(timer);
        resolve(true);
      },
      () => {
        clearTimeout(timer);
        resolve(false);
      }
    );
  });
}

module.exports = { stopProcessTree };
