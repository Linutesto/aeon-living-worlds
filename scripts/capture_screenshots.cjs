/*
 * Capture AEON dashboard screenshots into media/screenshots/.
 *
 * Requires: a running AEON server and a Chromium build driven via playwright-core.
 * Usage:
 *   node scripts/capture_screenshots.cjs
 * Env overrides:
 *   AEON_URL   (default http://localhost:8080)
 *   CHROME     path to Chrome/Chromium binary (default /usr/bin/google-chrome-stable)
 *   PW_CORE    path to a playwright-core install (default: resolved from node_modules)
 *   OUT        output dir (default media/screenshots)
 */
const path = require("path");
const fs = require("fs");

const URL = process.env.AEON_URL || "http://localhost:8080";
const CHROME = process.env.CHROME || "/usr/bin/google-chrome-stable";
const OUT = process.env.OUT || path.join(__dirname, "..", "media", "screenshots");
const PW_CORE = process.env.PW_CORE || "playwright-core";

fs.mkdirSync(OUT, { recursive: true });
let pw;
try { pw = require(PW_CORE); }
catch (e) {
  console.error(`Could not load playwright-core (${PW_CORE}). Set PW_CORE to a valid install.`);
  process.exit(2);
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, name) });
  console.log("  saved", name);
}
async function safe(label, fn) { try { await fn(); } catch (e) { console.warn("  skip", label, "-", e.message); } }

(async () => {
  const browser = await pw.chromium.launch({
    executablePath: CHROME,
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
           "--no-sandbox", "--ignore-gpu-blocklist"],
  });

  // ---- desktop captures ----
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(URL, { waitUntil: "networkidle" });
  await wait(7000);
  // a small camera nudge kicks chunk streaming so terrain is present for the first shot
  await safe("warmup-nudge", async () => {
    const c = await page.$("#world");
    const box = await c.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, -300); await wait(600);
    await page.mouse.wheel(0, 300);
  });
  await wait(6000);                                   // let the world grow + stream
  await shot(page, "01-world-overview.png");

  await safe("setup", async () => {
    await page.click('[data-tab="setup"]'); await wait(1200);
    await shot(page, "02-setup-panel.png");
    await page.click('#panel-grip').catch(() => {});
  });

  await safe("nations", async () => {
    await page.click('[data-overlay="political"]'); await wait(2500);
    await shot(page, "03-nations.png");
  });

  await safe("history", async () => {
    await page.click('[data-tab="timeline"]'); await wait(1500);
    await shot(page, "04-history.png");
    await page.click('#panel-grip').catch(() => {});
  });

  await safe("charts", async () => {
    await page.click('[data-tab="metrics"]'); await wait(1500);
    await shot(page, "05-charts.png");
    await page.click('#panel-grip').catch(() => {});
  });

  // texture-pack comparison: default vs snowy-ice-age
  await safe("texture-default", async () => {
    await page.click('[data-overlay="territory"]').catch(() => {});
    await wait(1500); await shot(page, "06-texture-pack-default.png");
  });
  await safe("texture-snowy", async () => {
    await page.click('[data-tab="setup"]'); await wait(800);
    await page.evaluate(() => document.querySelectorAll('#ws-root details.ws-card').forEach((d) => (d.open = true)));
    await wait(300);
    await page.locator('[data-key="texture_pack"]').scrollIntoViewIfNeeded();
    await page.selectOption('[data-key="texture_pack"]', "snowy-ice-age");
    await page.click('[data-act="apply-graphics"]');
    await wait(4500);
    await page.click('#panel-grip').catch(() => {});
    await wait(2500);
    await shot(page, "07-texture-pack-snowy.png");
  });

  await safe("city-closeup", async () => {
    await page.click('[data-cam="city"]').catch(() => {});
    await wait(4000);
    await shot(page, "08-city-closeup.png");
  });
  await page.close();

  // ---- mobile viewport (Pixel-9-ish portrait) ----
  await safe("mobile", async () => {
    const m = await browser.newPage({ viewport: { width: 412, height: 915 } });
    await m.goto(URL, { waitUntil: "networkidle" });
    await wait(8000);
    await shot(m, "09-mobile.png");
    await m.close();
  });

  await browser.close();
  console.log("done ->", OUT);
})().catch((e) => { console.error("FATAL", e); process.exit(1); });
