// Capture beautified UI screenshot to dist/lengrvis-ui-beautified.png
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const OUT = path.resolve(__dirname, "..", "dist", "lengrvis-ui-beautified.png");
const URL = process.env.LENGRVIS_URL || process.env.LENGRVIS_URL || process.env.LENGRVIS_URL || "http://127.0.0.1:4173";

(async () => {
  fs.mkdirSync(path.dirname(OUT), { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2
  });
  const page = await context.newPage();

  console.log("[lengrvis] navigating to", URL);
  await page.goto(URL, { waitUntil: "networkidle", timeout: 30000 });

  // wait for the office to render
  await page.waitForSelector(".office-agent", { timeout: 15000 });
  const agentCount = await page.locator(".office-agent").count();
  console.log(`[lengrvis] ${agentCount} agents visible`);

  // small settle delay for animations / fonts
  await page.waitForTimeout(800);

  await page.screenshot({ path: OUT, fullPage: false });
  console.log("[lengrvis] screenshot saved to", OUT);

  await browser.close();
})().catch((err) => {
  console.error("[lengrvis] error:", err);
  process.exit(1);
});
