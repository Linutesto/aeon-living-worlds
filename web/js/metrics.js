// metrics.js — the "Charts" panel: sparkline time-series for the world's vitals.
// Lightweight canvas sparklines (no chart lib) drawn from the `metrics` payload.

import { store } from "./ws.js";

let unsub = [];
const CHARTS = [
  ["population", "Population", "#7c5cff"],
  ["species_count", "Species", "#2fd6a8"],
  ["biodiversity", "Biodiversity", "#4ad06b"],
  ["civilization_count", "Civilizations", "#b89cff"],
  ["avg_temperature", "Avg temperature °C", "#ff8a5a"],
  ["world_health", "World health", "#ffcc66"],
];

export function render(root) {
  unsub.forEach((f) => f()); unsub = [];
  root.innerHTML = `<div class="panel-title">Charts 📈</div>
    <div class="panel-sub">Rolling history of the world's vitals.</div>
    ${CHARTS.map(([k, label]) => `
      <div class="card">
        <h4>${label} <span class="tl-tick" id="m-${k}-last"></span></h4>
        <canvas class="chart" id="m-${k}"></canvas>
      </div>`).join("")}`;

  unsub.push(store.on("metrics", ({ series }) => CHARTS.forEach(
    ([k, , color]) => draw(k, series[k] || [], color))));
}

function draw(key, samples, color) {
  const cv = document.getElementById(`m-${key}`);
  if (!cv) return;
  const dpr = Math.min(devicePixelRatio, 2);
  const w = cv.clientWidth * dpr, h = cv.clientHeight * dpr;
  cv.width = w; cv.height = h;
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  if (samples.length < 2) return;

  const ys = samples.map((s) => s[1]);
  const min = Math.min(...ys), max = Math.max(...ys);
  const span = max - min || 1;
  const px = (i) => (i / (samples.length - 1)) * w;
  const py = (v) => h - ((v - min) / span) * (h - 8) - 4;

  // area fill
  ctx.beginPath();
  ctx.moveTo(0, h);
  samples.forEach((s, i) => ctx.lineTo(px(i), py(s[1])));
  ctx.lineTo(w, h); ctx.closePath();
  ctx.fillStyle = color + "22"; ctx.fill();
  // line
  ctx.beginPath();
  samples.forEach((s, i) => i ? ctx.lineTo(px(i), py(s[1])) : ctx.moveTo(px(i), py(s[1])));
  ctx.strokeStyle = color; ctx.lineWidth = 2 * dpr; ctx.stroke();

  const last = document.getElementById(`m-${key}-last`);
  if (last) last.textContent = Intl.NumberFormat("en",
    { notation: "compact", maximumFractionDigits: 2 }).format(ys[ys.length - 1]);
}
