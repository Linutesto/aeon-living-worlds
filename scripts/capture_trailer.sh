#!/usr/bin/env bash
# Record a short AEON trailer GIF: drive the live dashboard with Playwright (records a
# webm), then convert to an optimized GIF with ffmpeg.
#
# Requires: a running AEON server, a Chromium build + playwright-core, and ffmpeg.
# Usage:    bash scripts/capture_trailer.sh
# Env:      AEON_URL (http://localhost:8080) · CHROME · PW_CORE · OUT (media/trailer)
set -uo pipefail
cd "$(dirname "$0")/.."

AEON_URL="${AEON_URL:-http://localhost:8080}"
CHROME="${CHROME:-/usr/bin/google-chrome-stable}"
PW_CORE="${PW_CORE:-$(pwd)/freeaitokens/node_modules/playwright-core}"
OUT="${OUT:-media/trailer}"
WORKDIR="$(mktemp -d)"
mkdir -p "$OUT"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found"; exit 2; }

echo "==> recording interaction (~16s) from $AEON_URL"
AEON_URL="$AEON_URL" CHROME="$CHROME" PW_CORE="$PW_CORE" WORKDIR="$WORKDIR" node <<'NODE'
const pw = require(process.env.PW_CORE);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const safe = async (f) => { try { await f(); } catch (e) { console.warn("  skip:", e.message); } };
(async () => {
  const browser = await pw.chromium.launch({ executablePath: process.env.CHROME,
    args: ["--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader",
           "--no-sandbox","--ignore-gpu-blocklist"] });
  const ctx = await browser.newContext({ viewport: { width: 960, height: 600 },
    recordVideo: { dir: process.env.WORKDIR, size: { width: 960, height: 600 } } });
  const page = await ctx.newPage();
  await page.goto(process.env.AEON_URL, { waitUntil: "networkidle" });
  await wait(7000);
  const c = await page.$("#world"); const b = await c.boundingBox();
  const cx = b.x + b.width/2, cy = b.y + b.height/2;
  // pan + zoom
  await safe(async () => { await page.mouse.move(cx, cy); await page.mouse.down();
    await page.mouse.move(cx-160, cy-90, { steps: 30 }); await page.mouse.up(); });
  await safe(async () => { await page.mouse.wheel(0, -350); }); await wait(1500);
  // nations overlay
  await safe(async () => { await page.click('[data-overlay="political"]'); }); await wait(2500);
  // open setup + switch texture pack
  await safe(async () => { await page.click('[data-tab="setup"]'); await wait(900);
    await page.evaluate(() => document.querySelectorAll('#ws-root details.ws-card').forEach(d=>d.open=true));
    await page.locator('[data-key="texture_pack"]').scrollIntoViewIfNeeded();
    await page.selectOption('[data-key="texture_pack"]', 'lush-green');
    await page.click('[data-act="apply-graphics"]'); await wait(3500);
    await page.click('#panel-grip'); });
  await wait(1500);
  // history then charts
  await safe(async () => { await page.click('[data-tab="timeline"]'); }); await wait(1800);
  await safe(async () => { await page.click('[data-tab="metrics"]'); }); await wait(1800);
  await ctx.close();   // flushes the webm
  await browser.close();
})().catch((e) => { console.error("FATAL", e); process.exit(1); });
NODE

VID="$(ls -t "$WORKDIR"/*.webm 2>/dev/null | head -1)"
[ -n "$VID" ] || { echo "no video recorded"; exit 1; }

echo "==> converting to GIF"
PAL="$WORKDIR/palette.png"
ffmpeg -y -i "$VID" -vf "fps=8,scale=480:-1:flags=lanczos,palettegen" "$PAL" >/dev/null 2>&1
ffmpeg -y -i "$VID" -i "$PAL" -lavfi "fps=8,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse" \
  "$OUT/aeon-living-worlds.gif" >/dev/null 2>&1

rm -rf "$WORKDIR"
echo "==> wrote $OUT/aeon-living-worlds.gif"
ls -lh "$OUT/aeon-living-worlds.gif"
