// godconsole.js — the "God" panel: direct interventions.
// Buttons come from the server's preset list and POST to /api/god/action. Each
// action funnels through the same validated directive path as the world-spirit, so
// nothing here can corrupt the world — it only bends its pressures.

import { api, post, store, health, send } from "./ws.js";
import { showToast } from "./toast.js";
import { setGraphicsPreset, getGraphicsPreset, getRenderOptions, setRenderOption } from "./omega/RendererApp.js";

const CATACLYSMS = new Set(["meteor_impact", "ice_age", "plague",
  "volcanic_eruption", "drought", "flood"]);

const GRAPHICS = [
  ["emergency", "Emergency"], ["low", "Low"], ["medium", "Medium"], ["high", "High"],
  ["ultra-4090", "RTX 4090 Ultra"], ["auto", "Auto"],
];
const ROUTE_LINES = [["off", "Off"], ["selected", "Selected"], ["important", "Important"], ["all", "All"]];
const TEXTURE_QUALITY = [["auto", "Auto"], ["512", "512"], ["1k", "1K"], ["2k", "2K"], ["4k", "4K"]];

const SPEEDS = [0.25, 0.5, 1, 2, 5, 10, 50, 100];

export async function render(root) {
  root.innerHTML = `
    <div class="panel-title">God Console ⚡</div>
    <div class="panel-sub">You shape the pressures. The world decides the rest.</div>
    ${settingsHtml()}
    <div class="card">
      <h4>World Saves</h4>
      <div class="save-row">
        <input id="save-slot" value="manual" maxlength="48" />
        <button id="save-btn" class="god-btn compact">Save</button>
      </div>
      <div id="save-list"><div class="empty">Loading save slots…</div></div>
    </div>
    <div class="btn-grid" id="god-grid"><div class="empty">Loading powers…</div></div>`;

  renderSaves();
  wireSettings();

  const presets = await api("/api/god/presets");
  const grid = document.getElementById("god-grid");
  if (!grid) return;
  grid.innerHTML = presets.map((p, i) => {
    const danger = p.kind && CATACLYSMS.has(p.kind);
    return `<button class="god-btn ${danger ? "cataclysm" : ""}" data-i="${i}">${esc(p.label)}</button>`;
  }).join("");

  grid.querySelectorAll(".god-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const p = presets[+btn.dataset.i];
      btn.disabled = true;
      const body = { op: p.op };
      if (p.key !== undefined) { body.key = p.key; body.value = p.value; }
      if (p.kind !== undefined) body.kind = p.kind;
      if (p.diet !== undefined) body.diet = p.diet;
      const res = await post("/api/god/action", body);
      showToast(res.ok ? `✓ ${res.message}` : `✗ ${res.message}`);
      setTimeout(() => (btn.disabled = false), 600);
    }));
}

function wireSettings() {
  const opts = getRenderOptions();
  const gfx = document.getElementById("gfx-preset");
  if (gfx) gfx.onchange = () => {
    setGraphicsPreset(gfx.value);
    showToast(`Graphics: ${gfx.options[gfx.selectedIndex].text}`);
  };
  document.querySelectorAll("[data-render-option]").forEach((el) => {
    const key = el.dataset.renderOption;
    if (el.type === "checkbox") el.checked = !!opts[key];
    else el.value = opts[key];
    const sync = () => {
      const value = el.type === "checkbox" ? el.checked : el.value;
      setRenderOption(key, value);
      const readout = document.querySelector(`[data-readout="${key}"]`);
      if (readout) readout.textContent = formatSettingValue(key, value);
    };
    el.addEventListener("input", sync);
    el.addEventListener("change", sync);
  });
  const speed = document.getElementById("default-speed");
  if (speed) {
    speed.value = localStorage.getItem("aeon.defaultSpeed") || "1";
    speed.onchange = () => {
      localStorage.setItem("aeon.defaultSpeed", speed.value);
      send({ action: "speed", speed: Number(speed.value) });
      showToast(`Default speed: ${speed.value}×`);
    };
  }
  // live FPS + API health while the panel is open
  const fpsEl = document.getElementById("set-fps");
  const apiEl = document.getElementById("set-api");
  const offFps = store.on("_fps", ({ fps }) => { if (fpsEl) fpsEl.textContent = `${fps} fps`; });
  const offHealth = store.on("_health", () => paintHealth(apiEl));
  paintHealth(apiEl);
  // stop updating once the panel is replaced
  const obs = new MutationObserver(() => {
    if (!document.getElementById("set-fps")) { offFps(); offHealth(); obs.disconnect(); }
  });
  const body = document.getElementById("panel-body");
  if (body) obs.observe(body, { childList: true });
}

function settingsHtml() {
  const opts = getRenderOptions();
  const opt = (key) => opts[key];
  const checked = (key) => opt(key) ? "checked" : "";
  const speed = localStorage.getItem("aeon.defaultSpeed") || "1";
  return `<div class="card settings-card">
    <h4>Settings</h4>
    <div class="settings-section">
      <div class="settings-head">Graphics</div>
      <label class="setting-row"><span>Quality</span><select id="gfx-preset">${GRAPHICS.map(([v, l]) =>
        `<option value="${v}" ${getGraphicsPreset() === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="setting-check"><input type="checkbox" data-render-option="shadows" ${checked("shadows")} /> Shadows</label>
      <label class="setting-check"><input type="checkbox" data-render-option="fog" ${checked("fog")} /> Fog</label>
      <label class="setting-check"><input type="checkbox" data-render-option="atmosphere" ${checked("atmosphere")} /> Atmosphere</label>
      <label class="setting-check"><input type="checkbox" data-render-option="particles" ${checked("particles")} /> Particles / crowds</label>
      <label class="setting-row"><span>Texture quality</span><select data-render-option="textureQuality">${TEXTURE_QUALITY.map(([v, l]) =>
        `<option value="${v}" ${String(opt("textureQuality")) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="setting-row"><span>DPR / render scale <b data-readout="dprScale">${formatSettingValue("dprScale", opt("dprScale"))}</b></span>
        <input type="range" min="0.5" max="2.5" step="0.05" data-render-option="dprScale" value="${opt("dprScale")}" /></label>
      <label class="setting-row"><span>FPS target <b data-readout="fpsTarget">${formatSettingValue("fpsTarget", opt("fpsTarget"))}</b></span>
        <input type="range" min="15" max="144" step="1" data-render-option="fpsTarget" value="${opt("fpsTarget")}" /></label>
    </div>
    <div class="settings-section">
      <div class="settings-head">Visual</div>
      <label class="setting-row"><span>Biome detail <b data-readout="biomeDetail">${formatSettingValue("biomeDetail", opt("biomeDetail"))}</b></span>
        <input type="range" min="0" max="1.5" step="0.05" data-render-option="biomeDetail" value="${opt("biomeDetail")}" /></label>
      <label class="setting-row"><span>Terrain detail <b data-readout="terrainDetail">${formatSettingValue("terrainDetail", opt("terrainDetail"))}</b></span>
        <input type="range" min="0" max="1.5" step="0.05" data-render-option="terrainDetail" value="${opt("terrainDetail")}" /></label>
      <label class="setting-row"><span>Building detail radius <b data-readout="buildingDetailRadius">${formatSettingValue("buildingDetailRadius", opt("buildingDetailRadius"))}</b></span>
        <input type="range" min="10" max="180" step="1" data-render-option="buildingDetailRadius" value="${opt("buildingDetailRadius")}" /></label>
      <label class="setting-row"><span>Citizen detail radius <b data-readout="citizenDetailRadius">${formatSettingValue("citizenDetailRadius", opt("citizenDetailRadius"))}</b></span>
        <input type="range" min="4" max="80" step="1" data-render-option="citizenDetailRadius" value="${opt("citizenDetailRadius")}" /></label>
      <label class="setting-row"><span>Road detail <b data-readout="roadDetail">${formatSettingValue("roadDetail", opt("roadDetail"))}</b></span>
        <input type="range" min="0" max="1.5" step="0.05" data-render-option="roadDetail" value="${opt("roadDetail")}" /></label>
      <label class="setting-row"><span>Vegetation density <b data-readout="vegetationDensity">${formatSettingValue("vegetationDensity", opt("vegetationDensity"))}</b></span>
        <input type="range" min="0" max="1.5" step="0.05" data-render-option="vegetationDensity" value="${opt("vegetationDensity")}" /></label>
      <label class="setting-row"><span>Agent / crowd density <b data-readout="agentCrowdDensity">${formatSettingValue("agentCrowdDensity", opt("agentCrowdDensity"))}</b></span>
        <input type="range" min="0" max="1.5" step="0.05" data-render-option="agentCrowdDensity" value="${opt("agentCrowdDensity")}" /></label>
      <label class="setting-row"><span>Overlay density <b data-readout="overlayDensity">${formatSettingValue("overlayDensity", opt("overlayDensity"))}</b></span>
        <input type="range" min="0" max="1.5" step="0.05" data-render-option="overlayDensity" value="${opt("overlayDensity")}" /></label>
      <label class="setting-row"><span>Influence opacity <b data-readout="influenceOverlayOpacity">${formatSettingValue("influenceOverlayOpacity", opt("influenceOverlayOpacity"))}</b></span>
        <input type="range" min="0" max="1.5" step="0.05" data-render-option="influenceOverlayOpacity" value="${opt("influenceOverlayOpacity")}" /></label>
      <label class="setting-row"><span>Route lines</span><select data-render-option="routeLines">${ROUTE_LINES.map(([v, l]) =>
        `<option value="${v}" ${String(opt("routeLines")) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="setting-row"><span>Route importance <b data-readout="routeImportance">${formatSettingValue("routeImportance", opt("routeImportance"))}</b></span>
        <input type="range" min="0" max="1" step="0.05" data-render-option="routeImportance" value="${opt("routeImportance")}" /></label>
      <label class="setting-row"><span>Render distance <b data-readout="renderDistance">${formatSettingValue("renderDistance", opt("renderDistance"))}</b></span>
        <input type="range" min="0.35" max="1.5" step="0.05" data-render-option="renderDistance" value="${opt("renderDistance")}" /></label>
      <label class="setting-check"><input type="checkbox" data-render-option="cinematicMode" ${checked("cinematicMode")} /> Cinematic mode</label>
      <label class="setting-check"><input type="checkbox" data-render-option="screenshotMode" ${checked("screenshotMode")} /> Screenshot mode</label>
    </div>
    <div class="settings-section">
      <div class="settings-head">Controls</div>
      <label class="setting-row"><span>Camera sensitivity <b data-readout="cameraSensitivity">${formatSettingValue("cameraSensitivity", opt("cameraSensitivity"))}</b></span>
        <input type="range" min="0.35" max="2.5" step="0.05" data-render-option="cameraSensitivity" value="${opt("cameraSensitivity")}" /></label>
      <label class="setting-check"><input type="checkbox" data-render-option="invertControls" ${checked("invertControls")} /> Invert controls</label>
    </div>
    <div class="settings-section">
      <div class="settings-head">World</div>
      <label class="setting-row"><span>Speed default</span><select id="default-speed">${SPEEDS.map((s) =>
        `<option value="${s}" ${String(s) === String(speed) ? "selected" : ""}>${s}×</option>`).join("")}</select></label>
      <label class="setting-row"><span>UI scale <b data-readout="uiScale">${formatSettingValue("uiScale", opt("uiScale"))}</b></span>
        <input type="range" min="0.85" max="1.25" step="0.05" data-render-option="uiScale" value="${opt("uiScale")}" /></label>
    </div>
    <div class="settings-section">
      <div class="settings-head">Debug</div>
      <label class="setting-check"><input type="checkbox" data-render-option="showFps" ${checked("showFps")} /> FPS</label>
      <label class="setting-check"><input type="checkbox" data-render-option="chunkBorders" ${checked("chunkBorders")} /> Chunk borders</label>
      <label class="setting-check"><input type="checkbox" data-render-option="lodVisualization" ${checked("lodVisualization")} /> LOD visualization</label>
      <div class="row"><span class="k">Renderer</span><span class="v" id="set-fps">— fps</span></div>
      <div class="row"><span class="k">API</span><span class="v" id="set-api">checking…</span></div>
    </div>
  </div>`;
}

function formatSettingValue(key, value) {
  if (["uiScale", "cameraSensitivity", "biomeDetail", "terrainDetail", "roadDetail",
    "vegetationDensity", "agentCrowdDensity", "overlayDensity", "influenceOverlayOpacity",
    "routeImportance", "renderDistance",
    "dprScale"].includes(key)) {
    return `${Number(value).toFixed(2)}×`;
  }
  if (key === "buildingDetailRadius" || key === "citizenDetailRadius") return `${Math.round(Number(value))}m`;
  if (key === "fpsTarget") return `${Math.round(Number(value))} fps`;
  return String(value);
}

function paintHealth(el) {
  if (!el) return;
  if (health.ok) { el.textContent = `ok · ${health.latencyMs}ms`; el.style.color = "#7be0a0"; }
  else { el.textContent = `error: ${health.lastError || "offline"}`; el.style.color = "#ff8a8a"; }
}

async function renderSaves() {
  const list = document.getElementById("save-list");
  const slotInput = document.getElementById("save-slot");
  const saveBtn = document.getElementById("save-btn");
  if (!list || !slotInput || !saveBtn) return;
  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    const res = await post("/api/save", { slot: slotInput.value.trim() || "manual" });
    showToast(`Saved ${res.slot} at tick ${res.tick}`);
    await renderSaves();
    saveBtn.disabled = false;
  };
  const data = await api("/api/saves");
  const slots = data.slots || [];
  if (!slots.length) {
    list.innerHTML = `<div class="empty">No saves yet. Autosave starts after the configured tick interval.</div>`;
    return;
  }
  list.innerHTML = slots.map((s) => `
    <div class="save-item">
      <div>
        <b>${esc(s.slot)}</b>
        <div class="tl-detail">tick ${s.tick} · ${s.summary?.cities || 0} cities · ${s.summary?.people || 0} people</div>
      </div>
      <button class="icon-btn" data-load="${esc(s.slot)}" title="Load save">↺</button>
    </div>`).join("");
  list.querySelectorAll("[data-load]").forEach((btn) => {
    btn.onclick = async () => {
      btn.disabled = true;
      const slot = btn.dataset.load;
      const res = await post("/api/load", { slot });
      showToast(res.loaded ? `Loaded ${slot} at tick ${res.tick}` : `Could not load ${slot}`);
      await renderSaves();
      btn.disabled = false;
    };
  });
}

function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
}[c])); }
