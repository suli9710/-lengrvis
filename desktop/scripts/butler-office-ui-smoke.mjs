// Isolated renderer fixture: browser UI is real, API data is explicitly controlled.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "@playwright/test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(root, "../.tmp/audit-fixes/butler-office-ui.png");
const server = await createServer({ root, configFile: resolve(root, "vite.config.ts"),
  server: { host: "127.0.0.1", port: 0, strictPort: false }, logLevel: "error" });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/scripts/fixtures/butler-ui.html`);
  const card = page.locator(".butler-matter").filter({ has: page.getByRole("heading", { name: "公开网页整理为 Excel 与 Word 报告", exact: true }) });
  await card.getByText("公开资料整理.xlsx").waitFor();
  assert.equal(await card.getByRole("button", { name: "在 Excel 中打开" }).isEnabled(), true);
  assert.equal(await card.getByRole("button", { name: "在 Word 中打开" }).isDisabled(), true);
  await card.getByRole("button", { name: "在 Excel 中打开" }).click();
  await page.waitForFunction(() => window.butlerAcceptance.actions.some(action => action.openArtifact));
  assert.deepEqual(await page.evaluate(() => window.butlerAcceptance.actions.find(action => action.openArtifact)),
    { openArtifact: { matter_id: "matter_office", artifact_id: "artifact_xlsx", version: 1 } });
  await mkdir(dirname(output), { recursive: true });
  await card.screenshot({ path: output });
  await page.setViewportSize({ width: 600, height: 900 });
  assert.equal(await card.evaluate(element => element.scrollWidth <= element.clientWidth), true);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ ok: true, screenshot: output, checks: ["verified entry", "changed output blocked", "ID and version", "narrow layout"] }));
} finally {
  await browser?.close();
  await server.close();
}
