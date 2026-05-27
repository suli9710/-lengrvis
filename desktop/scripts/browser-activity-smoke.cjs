const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const path = require("node:path");
const { chromium } = require("@playwright/test");

const previewUrl = "http://127.0.0.1:4173";
const desktopRoot = path.resolve(__dirname, "..");

const session = {
  id: "backend-only-session",
  task_id: "task-browser",
  current_url: "https://example.com/search?q=mavris",
  title: "Backend-only Browser Session",
  status: "running",
  mode: "agent",
  created_at: "2026-05-27T00:00:00.000Z",
  updated_at: "2026-05-27T00:01:00.000Z",
  paused: false,
  takeover: false,
  last_observation: null
};

const event = {
  id: "backend-event-1",
  session_id: session.id,
  task_id: session.task_id,
  type: "action.observe",
  action: { kind: "observe" },
  url: session.current_url,
  title: session.title,
  ok: true,
  created_at: "2026-05-27T00:01:30.000Z"
};

function assertNoSecretPayload(value, label) {
  const text = JSON.stringify(value);
  assert.equal(text.includes("secret-token"), false, `${label} should not include raw token values`);
  assert.equal(text.includes("password123"), false, `${label} should not include raw form text`);
  assert.equal(text.includes("top-secret observation"), false, `${label} should not include raw observed page text`);
  assert.equal(text.includes("#password"), false, `${label} should not include sensitive selectors`);
}

function startPreview() {
  console.log("starting Vite preview on 127.0.0.1:4173");
  const viteBin = path.join(desktopRoot, "node_modules", "vite", "bin", "vite.js");
  const child = spawn(process.execPath, [viteBin, "preview", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], {
    cwd: desktopRoot,
    stdio: ["ignore", "pipe", "pipe"]
  });
  child.stdout.on("data", (data) => process.stdout.write(data));
  child.stderr.on("data", (data) => process.stderr.write(data));
  return child;
}

async function waitForPreview() {
  console.log("waiting for Vite preview");
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(previewUrl);
      if (response.ok) return;
    } catch {
      // Keep polling until Vite preview is ready.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("Vite preview did not start in time");
}

async function installApiMocks(page) {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:8000") {
      await route.continue();
      return;
    }

    const json = (body) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body)
    });

    if (url.pathname === "/api/health") return json({ status: "ok" });
    if (url.pathname === "/api/browser/sessions") return json({ ok: true, sessions: [session] });
    if (url.pathname === `/api/browser/session/${session.id}/events`) return json({ ok: true, events: [event] });
    if (url.pathname === "/api/browser/observe") return json({ ok: true, event });
    if (url.pathname === "/api/browser/replay-export") return json({ ok: true, events: [event], session });
    if (url.pathname === "/api/chat/messages") return json([]);
    if (url.pathname === "/api/tasks") return json([]);
    if (url.pathname === "/api/runs") return json([]);
    if (url.pathname === "/api/current-plan") return json({});
    if (url.pathname === "/api/settings") return json({});
    if (url.pathname === "/api/settings/llm/health") return json({});
    if (url.pathname === "/api/settings/llm/cost-summary") return json({});
    if (url.pathname === "/api/context/usage") return json({});
    if (url.pathname === "/api/audit/logs") return json([]);
    if (url.pathname === "/api/system/info") return json({});
    if (url.pathname === "/api/chat/proactive-suggestions") return json([]);
    if (url.pathname.endsWith("/agent-messages")) return json([]);
    if (url.pathname.endsWith("/safety-reviews")) return json([]);
    if (url.pathname === "/api/approvals/pending") return json([]);

    return json({});
  });
}

async function assertRootRendered(page) {
  await page.waitForSelector("#root > *", { timeout: 15_000 });
  const rootText = await page.locator("#root").innerText();
  assert.ok(rootText.trim().length > 0, "root should not be blank");
  await assertButtonExists(page, "Refresh");
}

async function assertButtonExists(page, name) {
  await page.getByRole("button", { name }).first().waitFor({ timeout: 10_000 });
}

(async () => {
  const preview = startPreview();
  let browser;
  try {
    await waitForPreview();
    console.log("launching Chromium");
    browser = await chromium.launch();

    for (const viewport of [
      { width: 1366, height: 768, label: "desktop" },
      { width: 390, height: 844, label: "mobile" }
    ]) {
      const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      await installApiMocks(page);
      await page.goto(previewUrl, { waitUntil: "networkidle" });
      await assertRootRendered(page);
      await assertButtonExists(page, "Chat");
      console.log(`viewport smoke passed: ${viewport.label} ${viewport.width}x${viewport.height}`);
      await page.close();
    }

    console.log("checking Browser Activity backend-only session");
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    await installApiMocks(page);
    await page.goto(`${previewUrl}/?view=browser`, { waitUntil: "networkidle" });
    await assertRootRendered(page);

    await page.getByText(session.title).first().waitFor({ timeout: 10_000 });
    await page.getByText("action.observe").waitFor({ timeout: 10_000 });

    for (const name of ["Pause", "Take Over", "Stop"]) {
      await assert.equal(await page.getByRole("button", { name }).isDisabled(), true, `${name} should be disabled without an Electron host session`);
    }
    await assert.equal(await page.getByRole("button", { name: "Hide" }).isDisabled(), true, "Hide should be disabled when Electron host is absent");

    const requestCountBefore = await page.evaluate(() => window.__browserHostCalls ?? 0);
    await page.getByRole("button", { name: "Pause" }).click({ force: true });
    const requestCountAfter = await page.evaluate(() => window.__browserHostCalls ?? 0);
    assert.equal(requestCountAfter, requestCountBefore, "disabled host-only controls should not call Electron host actions");

    console.log("Browser Activity backend-only smoke passed");
    await page.close();

    console.log("checking BrowserHost output redaction");
    const Module = require("node:module");
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
      if (request === "electron") {
        return {
          BrowserWindow: {
            fromWebContents: (sender) => sender && sender.__trustedWindow ? sender.__trustedWindow : null
          },
          BrowserView: class BrowserView {},
          WebContentsView: class WebContentsView {},
          ipcMain: { handle: () => undefined },
          shell: { openExternal: async () => undefined }
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    try {
      const { BrowserHost } = require("../dist/main/browserHost.js");
      const host = new BrowserHost(() => null);
      host.sessions = new Map([
        [
          "sensitive",
          {
            container: {},
            session: {
            id: "sensitive",
            current_url: "https://example.test/callback?token=secret-token&safe=1",
            title: "Sensitive",
            status: "idle",
            mode: "watch",
            created_at: "2026-05-27T00:00:00.000Z",
            updated_at: "2026-05-27T00:00:00.000Z",
            paused: false,
            takeover: false,
            last_observation: {
              url: "https://example.test/page?code=secret-token&client_secret=secret-token&session_id=secret-token",
              text: "top-secret observation token=secret-token password=password123",
              links: [{ text: "top-secret observation", url: "https://example.test/a?password=secret-token&oauth_token=secret-token" }]
            }
            },
            events: [{
              id: "event-sensitive",
              session_id: "sensitive",
              type: "action.fill",
              action: { kind: "fill", selector: "#password", text: "password123", fields: { "#password": "password123" } },
              url: "https://example.test/form?token=secret-token&auth_token=secret-token",
              ok: true,
              screenshot_url: "data:image/png;base64,secret-token",
              created_at: "2026-05-27T00:00:01.000Z"
            }]
          }
        ]
      ]);
      const redactedSnapshot = host.getSnapshot();
      assertNoSecretPayload(redactedSnapshot, "BrowserHost snapshot");
      console.log("BrowserHost redaction smoke passed");
    } finally {
      Module._load = originalLoad;
    }
  } finally {
    if (browser) await browser.close();
    preview.kill("SIGTERM");
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
