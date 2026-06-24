// perfhud.js — a toggleable on-screen performance HUD for the Omega renderer.
//
// Everything shown here is read from data the renderer already measures (it emits an
// `_fps` event each second and folds rich renderer counters into the governor store
// under `omega_renderer`), plus the API client's last latency and the engine's sim
// tick cost. Nothing is invented. Toggle with the `P` key or the on-screen button.

import { store } from "./ws.js";
import { health } from "./ws.js";

let el = null;
let visible = false;
const last = { fps: 0, quality: "—", render_ms: 0, preset: "auto", terrain_lod: "—",
               pixel_ratio: 1, triangles: 0, draw_calls: 0 };

export function initPerfHud() {
  el = document.createElement("div");
  el.id = "perf-hud";
  el.style.cssText = [
    "position:fixed", "top:8px", "right:8px", "z-index:9999",
    "font:11px/1.45 ui-monospace,Menlo,Consolas,monospace",
    "background:rgba(10,12,20,0.82)", "color:#cfe0ff",
    "border:1px solid rgba(120,150,255,0.32)", "border-radius:8px",
    "padding:8px 10px", "min-width:188px", "backdrop-filter:blur(4px)",
    "pointer-events:none", "display:none", "white-space:pre",
  ].join(";");
  document.body.appendChild(el);

  // collect from the per-second render event
  store.on("_fps", (d) => {
    Object.assign(last, d);
    if (visible) paint();
  });

  // P toggles the HUD (ignored while typing in an input)
  addEventListener("keydown", (e) => {
    if (e.key === "p" || e.key === "P") {
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      togglePerfHud();
    }
  });
}

export function togglePerfHud(force) {
  visible = typeof force === "boolean" ? force : !visible;
  if (el) el.style.display = visible ? "block" : "none";
  if (visible) paint();
}

function row(label, value) {
  return `${label.padEnd(11)}${String(value)}`;
}

function paint() {
  if (!el) return;
  const r = store.state.governor?.omega_renderer || {};
  const perf = store.state.governor?.perf || {};
  const fpsColor = last.fps >= 55 ? "#7be0a0" : last.fps >= 30 ? "#ffcc66" : "#ff6b6b";
  const texMb = r.textures != null ? `~${Math.max(1, r.textures)} tex` : "—";
  el.innerHTML =
    `<b style="color:#9fb6ff">⚙ RENDER HUD</b>  <span style="color:#7a88aa">[P]</span>\n` +
    row("fps", `<span style="color:${fpsColor}">${last.fps}</span>  ${last.quality}`) + "\n" +
    row("preset", `${last.preset}  lod${last.terrain_lod}  x${last.pixel_ratio}`) + "\n" +
    row("render ms", last.render_ms) + "\n" +
    row("draw calls", last.draw_calls || r.draw_calls_estimate || 0) + "\n" +
    row("triangles", fmt(last.triangles)) + "\n" +
    row("meshes", `${r.mesh_count ?? "—"} (${r.instanced_meshes ?? 0} inst)`) + "\n" +
    row("geometry", r.geometries ?? "—") + "\n" +
    row("materials", r.materials ?? "—") + "\n" +
    row("textures", texMb) + "\n" +
    row("chunks", `${r.chunks_visible ?? "—"} vis / ${r.chunks_loading ?? 0} load`) + "\n" +
    row("chunk q", `${r.chunks_cached ?? 0} cache  ${r.chunks_built_per_second ?? 0}/s`) + "\n" +
    row("js heap", r.js_heap_mb != null ? `${r.js_heap_mb} MB` : "—") + "\n" +
    row("sim tick", perf.sim_tick_ms != null ? `${perf.sim_tick_ms} ms` : "—") + "\n" +
    row("api", `${health.latencyMs} ms ${health.ok ? "" : "⚠"}`);
}

function fmt(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
