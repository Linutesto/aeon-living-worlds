// worldsettings.js — the "Setup" panel: New World / Restart, Graphics, Civilizations,
// Texture Packs, and Debug Placement. Mobile-first (Pixel 9 portrait): full-width
// controls, big tap targets. Binds to /api/world/config/schema + the restart/graphics/
// texture endpoints, and drives the renderer directly for instant graphics/pack feedback.

import { api, post } from "./ws.js";
import { setGraphicsPreset, setTexturePack, setRenderOption,
         getRenderOptions } from "./omega/RendererApp.js";
import { showToast } from "./toast.js";

let schema = null;
let current = null;
let graphics = null;
let packs = null;

export async function render(root) {
  root.innerHTML = `
    <div class="panel-title">World Setup ⚙</div>
    <div class="panel-sub">Generate, restart, and dress the world.</div>
    <div id="ws-root" class="ws">Loading…</div>`;
  const host = root.querySelector("#ws-root");
  try {
    [schema, current, graphics, packs] = await Promise.all([
      api("/api/world/config/schema"), api("/api/world/config"),
      api("/api/graphics/presets"), api("/api/texture-packs"),
    ]);
  } catch (e) {
    host.innerHTML = `<div class="empty">World config API unavailable.</div>`;
    return;
  }
  host.innerHTML = html();
  wire(host);
}

function field(f, val) {
  const v = val ?? f.default;
  if (f.type === "enum") {
    const opts = f.options.map((o) =>
      `<option value="${esc(o)}"${o === v ? " selected" : ""}>${esc(o)}</option>`).join("");
    return `<label class="ws-field"><span>${esc(f.key)}</span>
      <select data-key="${esc(f.key)}">${opts}</select></label>`;
  }
  if (f.type === "str") {
    return `<label class="ws-field"><span>${esc(f.key)}</span>
      <input type="text" data-key="${esc(f.key)}" value="${esc(v)}"></label>`;
  }
  const step = f.type === "int" ? 1 : 0.01;
  return `<label class="ws-field" title="${esc(f.desc || "")}">
    <span>${esc(f.key)} <i>${esc(f.desc || "")}</i></span>
    <input type="number" data-key="${esc(f.key)}" value="${num(v)}"
      min="${num(f.lo)}" max="${num(f.hi)}" step="${step}"></label>`;
}

function html() {
  const struct = schema.structural.map((f) => field(f, current[f.key])).join("");
  const params = schema.params.map((f) => field(f, current.params?.[f.key])).join("");
  const pres = schema.presentation;
  const presetField = field(pres.find((f) => f.key === "graphics_preset"),
                            graphics.current);
  const packField = field(pres.find((f) => f.key === "texture_pack"), packs.current);
  const budgets = pres.filter((f) => !["graphics_preset", "texture_pack"].includes(f.key))
    .map((f) => field(f, current.presentation?.[f.key])).join("");
  const ro = getRenderOptions();

  return `
  <details class="ws-card" open><summary>🌍 New World / Restart</summary>
    <div class="ws-grid">${struct}</div>
    <details class="ws-sub"><summary>Generation knobs (climate, water, resources, war…)</summary>
      <div class="ws-grid">${params}</div>
    </details>
    <label class="ws-toggle"><input type="checkbox" id="ws-keepminds">
      <span>Keep trained minds across restart</span></label>
    <div class="ws-actions">
      <button class="ws-btn primary" data-act="restart">↻ Restart with these settings</button>
      <button class="ws-btn" data-act="same-seed">⟳ Restart (same seed)</button>
      <button class="ws-btn" data-act="random">🎲 Restart (random seed)</button>
    </div>
    <div class="ws-actions">
      <button class="ws-btn ghost" data-layer="civilization">Reset civilizations</button>
      <button class="ws-btn ghost" data-layer="terrain_climate">Reset terrain/climate</button>
      <button class="ws-btn ghost" data-layer="cities_population">Reset cities/pop</button>
      <button class="ws-btn ghost" data-layer="minds">Reset minds</button>
    </div>
  </details>

  <details class="ws-card"><summary>🏛 Civilizations</summary>
    <div class="panel-sub">Each civ is seeded from a distinct archetype (ideology, economy,
      war &amp; expansion bias). Set how many rival nations open the world:</div>
    <div class="ws-grid">${field(schema.structural.find((f) => f.key === "start_civilizations"),
                                   current.start_civilizations)}</div>
    <div class="ws-note">Changing this takes effect on the next restart.</div>
  </details>

  <details class="ws-card"><summary>🎨 Graphics</summary>
    <div class="ws-grid">${presetField}${packField}</div>
    <div class="ws-grid">${budgets}</div>
    <div class="ws-actions">
      <button class="ws-btn" data-act="apply-graphics">Apply graphics + pack</button>
    </div>
    <label class="ws-toggle"><input type="checkbox" id="ws-placementdbg"
      ${ro.placementDebug ? "checked" : ""}>
      <span>Debug placement (tint un-placeable buildings red)</span></label>
  </details>`;
}

function collect(host) {
  const cfg = { params: {}, presentation: {} };
  const structKeys = new Set(schema.structural.map((f) => f.key));
  const paramKeys = new Set(schema.params.map((f) => f.key));
  const presKeys = new Set(schema.presentation.map((f) => f.key));
  host.querySelectorAll("[data-key]").forEach((el) => {
    const k = el.dataset.key;
    let v = el.type === "number" ? Number(el.value) : el.value;
    if (structKeys.has(k)) cfg[k] = v;
    else if (paramKeys.has(k)) cfg.params[k] = Number(v);
    else if (presKeys.has(k)) cfg.presentation[k] = v;
  });
  return cfg;
}

function wire(host) {
  const keep = () => host.querySelector("#ws-keepminds")?.checked || false;

  host.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    const act = b.dataset.act;
    b.disabled = true;
    try {
      if (act === "restart") {
        await doRestart({ config: collect(host), keep_minds: keep() });
      } else if (act === "same-seed") {
        await doRestart({ config: { ...collect(host), seed: current.seed }, keep_minds: keep() });
      } else if (act === "random") {
        const r = await post("/api/world/restart/random",
                             { config: collect(host), keep_minds: keep() });
        afterRestart(r);
      } else if (act === "apply-graphics") {
        await applyGraphics(host);
      }
    } catch (e) { showToast("Action failed: " + e.message); }
    b.disabled = false;
  }));

  host.querySelectorAll("[data-layer]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await post("/api/world/reset-layer", { layer: b.dataset.layer });
      if (r.error) showToast(r.error);
      else showToast(`Reset ${b.dataset.layer.replace("_", "/")} ✓`);
    } catch (e) { showToast("Reset failed: " + e.message); }
    b.disabled = false;
  }));

  host.querySelector("#ws-placementdbg")?.addEventListener("change", (e) => {
    setRenderOption("placementDebug", e.target.checked);
  });
}

async function doRestart(body) {
  const r = await post("/api/world/restart", body);
  afterRestart(r);
}

function afterRestart(r) {
  if (r.error) { showToast(r.error); return; }
  showToast(`World restarted · seed ${r.seed} · ${r.civilizations} civs`);
  api("/api/world/config").then((c) => { current = c; });
}

async function applyGraphics(host) {
  const preset = host.querySelector('[data-key="graphics_preset"]')?.value;
  const pack = host.querySelector('[data-key="texture_pack"]')?.value;
  const budgets = {};
  host.querySelectorAll("[data-key]").forEach((el) => {
    const k = el.dataset.key;
    if (!["graphics_preset", "texture_pack"].includes(k) &&
        schema.presentation.some((f) => f.key === k)) budgets[k] = Number(el.value);
  });
  if (preset) {
    await post("/api/graphics/preset", { preset, ...budgets });
    try { setGraphicsPreset(preset); } catch (e) { /* renderer not ready */ }
  }
  if (pack) {
    await post("/api/texture-pack", { pack });
    try { await setTexturePack(pack); } catch (e) { /* renderer not ready */ }
  }
  showToast(`Applied ${preset} · ${pack}`);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function num(v) { return Number.isFinite(Number(v)) ? Number(v) : 0; }
