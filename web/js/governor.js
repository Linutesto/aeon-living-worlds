// governor.js — the "Spirit" panel: the AI governor's mind laid bare.
// Philosophy, current goal + reason, the live parameter knobs it has set, and a
// feed of recent decisions. Reads the `governor` payload.

import { api, store } from "./ws.js";

let unsub = [];
const PARAM_LABELS = {
  rainfall_multiplier: "Rainfall", temperature_bias: "Temp bias",
  storm_intensity: "Storms", sea_level: "Sea level",
  volcanic_activity: "Volcanism", tectonic_drift: "Tectonics",
  resource_richness: "Resources", plant_growth: "Plant growth",
  prey_fertility: "Prey fertility", predator_fertility: "Predator fertility",
  mutation_rate: "Mutation", carrying_capacity: "Carrying cap",
  civ_expansion_drive: "Expansion", war_propensity: "War", tech_progress: "Tech",
};

export function render(root) {
  unsub.forEach((f) => f()); unsub = [];
  root.innerHTML = `
    <div class="panel-title">The World-Spirit 🜂</div>
    <div class="panel-sub" id="gov-status">Listening for the spirit…</div>
    <div class="card"><div class="reason" id="gov-philosophy"></div></div>
    <div class="card">
      <h4>Current Goal</h4>
      <div id="gov-goal">—</div>
      <div class="reason" id="gov-goal-reason"></div>
    </div>
    <div class="panel-title" style="font-size:15px">Active World Policies</div>
    <div class="card" id="gov-params"></div>
    <div class="panel-title" style="font-size:15px">Species Minds (Level 2)</div>
    <div class="card" id="gov-species"></div>
    <div class="panel-title" style="font-size:15px">Society Mind — Liquid Student 🧠 (Level 3)</div>
    <div class="card" id="gov-mind"><div class="empty">The student has not woken yet.</div></div>
    <div class="panel-title" style="font-size:15px">Renderer Stability</div>
    <div class="card" id="gov-renderer"></div>
    <div class="card" id="policy-inspector"><div class="empty">Loading policy replay…</div></div>
    <div class="panel-title" style="font-size:15px">Recent Decisions</div>
    <div id="gov-decisions"><div class="empty">No decisions yet.</div></div>`;

  unsub.push(store.on("governor", render2));
  renderPolicyInspector();
}

function render2(g) {
  set("gov-status", g.online === false
    ? "⚠ Ollama offline — the fallback spirit is improvising."
    : g.online ? "● Connected to the local model." : "Waiting…");
  set("gov-philosophy", g.philosophy || "");
  set("gov-goal", g.goal || "—");
  set("gov-goal-reason", g.goal_reason || g.thought || "");

  const params = g.params || {};
  document.getElementById("gov-params").innerHTML = Object.entries(params).map(
    ([k, v]) => `<div class="row"><span class="k">${PARAM_LABELS[k] || k}</span>
      <span class="v">${(+v).toFixed(2)}</span></div>`).join("");

  const ai = g.species_ai || {}; const pool = g.pool || {};
  const sp = document.getElementById("gov-species");
  if (sp) sp.innerHTML = [
    ["Backend", ai.backend || "—"],
    ["Species policies", ai.species ?? 0],
    ["Replay samples", ai.samples ?? 0],
    ["Samples collected", ai.samples_collected ?? 0],
    ["Training batches", ai.batches ?? 0],
    ["Training updates", ai.updates ?? 0],
    ["Last loss", ai.last_loss ?? "—"],
    ["Policy confidence", ai.confidence ?? "—"],
    ["Living individuals", pool.people ?? 0],
    ["Cities in focus", pool.focused ?? 0],
  ].map(([k, v]) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("")
    + behaviorRows(ai.behavior_delta || {});

  renderMind(g.society_mind || {});

  const render = document.getElementById("gov-renderer");
  const omega = g.omega_renderer || {};
  if (render) render.innerHTML = [
    ["Quality mode", omega.quality_mode || omega.profile || "—"],
    ["LOD", omega.lod ?? "—"],
    ["Chunks visible", omega.chunks_visible ?? "—"],
    ["Chunks loading", omega.chunks_loading ?? "—"],
    ["Chunks cached", omega.chunks_cached ?? "—"],
    ["Build queue", omega.chunk_build_queue ?? "—"],
    ["Built/sec", omega.chunks_built_per_second ?? "—"],
    ["Stale layers", omega.stale_chunks ?? "—"],
    ["Draw calls", omega.draw_calls_estimate ?? "—"],
    ["Meshes", omega.mesh_count ?? "—"],
    ["Instanced meshes", omega.instanced_meshes ?? "—"],
    ["Geometries", omega.geometries ?? "—"],
    ["Materials", omega.materials ?? "—"],
    ["Textures", omega.textures ?? "—"],
    ["JS heap", omega.js_heap_mb == null ? "—" : `${omega.js_heap_mb} MB`],
  ].map(([k, v]) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");

  const dec = document.getElementById("gov-decisions");
  const list = (g.recent_decisions || []).slice().reverse();
  if (list.length) {
    dec.innerHTML = list.map((d) => `
      <div class="card">
        <h4>Tick ${d.tick}</h4>
        <div>${esc(d.thought || "(no comment)")}</div>
        ${(d.directives || []).map((x) => `<div class="reason">→ ${esc(x)}</div>`).join("")}
      </div>`).join("");
  }
}

async function renderPolicyInspector() {
  const el = document.getElementById("policy-inspector");
  if (!el) return;
  try {
    const p = await api("/api/policy/inspect");
    const actions = Object.entries(p.action_distribution || {}).slice(0, 8);
    const kinds = Object.entries(p.sample_kinds || {}).slice(0, 6);
    el.innerHTML = `
      <h4>Policy Replay</h4>
      <div class="row"><span class="k">Samples</span><span class="v">${p.samples}</span></div>
      <div class="row"><span class="k">Reward mean</span><span class="v">${p.recent_reward_mean}</span></div>
      <div class="row"><span class="k">Reward range</span><span class="v">${p.recent_reward_min}..${p.recent_reward_max}</span></div>
      <h4 style="margin-top:10px">Action Distribution</h4>
      ${actions.map(([k, v]) => `<div class="row"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`).join("") || "<div class='empty'>No actions yet.</div>"}
      <h4 style="margin-top:10px">Training Sources</h4>
      ${kinds.map(([k, v]) => `<div class="row"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`).join("") || "<div class='empty'>No samples yet.</div>"}
    `;
  } catch {
    el.innerHTML = `<div class="empty">Policy replay unavailable.</div>`;
  }
}

// Society Mind (Level 3): the live teacher→student distillation. Shows the student
// learning (loss sparkline, agreement, GPU "sweat") and progressively taking over the
// population (the student/teacher/utility bar), plus the 27B teacher's cohort activity.
function renderMind(sm) {
  const el = document.getElementById("gov-mind");
  if (!el) return;
  if (!sm || sm.enabled === false) {
    el.innerHTML = `<div class="empty">Society mind disabled (no GPU / torch, or off in config).</div>`;
    return;
  }
  const ds = sm.dataset || {}, byCh = ds.by_channel || {};
  const tea = sm.teacher || {}, mix = sm.population_mix || {};
  const sp = sm.spatial || {};
  const curve = sm.loss_curve || [];
  const gate = sm.confidence_gate || 0.45;
  const agreePct = Math.round((sm.agreement || 0) * 100);
  const sharePct = Math.round((sm.student_share || 0) * 100);
  const teacherPct = Math.round((sm.teacher_sampling_ratio ?? 1) * 100);
  const cur = sm.curriculum || {};
  const ck = sm.checkpoint || {};
  const rows = [
    ["Backend", sm.backend || "—"],
    ["Student size", `${esc(sm.student_size || "tiny")} · ${sm.hidden ?? "?"}×${sm.layers ?? "?"} · ${fmt(sm.params)} params`],
    ["GPU sweat", sm.gpu_mb ? `${sm.gpu_mb} MB` : "—"],
    ["Active embodied", `${fmt(sm.active_embodied_citizens)} max · ${fmt((mix.student||0))} now`],
    ["Train steps", fmt(sm.steps)],
    ["Samples trained", fmt(sm.samples_trained)],
    ["Loss (EMA)", sm.ema_loss ?? "—"],
    ["Action acc", pct(sm.action_acc)],
    ["Emotion acc", pct(sm.emotion_acc)],
    ["Intent acc", pct(sm.intent_acc)],
    ["Target acc", pct(sm.target_acc)],
  ];
  el.innerHTML = `
    ${sparkline(curve)}
    <div class="row"><span class="k">Teacher agreement</span>
      <span class="v" style="color:var(--accent-2)">${agreePct}%</span></div>
    ${meter(agreePct, gate * 100)}
    ${rows.map(([k, v]) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("")}
    <h4 style="margin-top:10px">Teacher-Priority Curriculum</h4>
    <div class="row"><span class="k">Phase</span><span class="v">${esc(sm.curriculum_phase || "phase_1_teacher_first")} (${(cur.phase_index ?? 0) + 1}/${cur.phase_count || 4})</span></div>
    <div class="row"><span class="k">Teacher sampling</span><span class="v">${teacherPct}%</span></div>
    <div class="row"><span class="k">Student autonomy</span><span class="v">${sharePct}% / ceiling ${pct(sm.autonomy_ratio)}</span></div>
    <div class="row"><span class="k">Rollbacks</span><span class="v">${fmt(sm.rollbacks)}</span></div>
    <div class="row"><span class="k">Blocked by</span><span class="v">${(cur.blocked_by || []).map(esc).join(", ") || "—"}</span></div>
    <div class="row"><span class="k">Drift score</span><span class="v">${sm.regression_drift_score ?? "—"}</span></div>
    <div class="row"><span class="k">Capability score</span><span class="v">${pct(sm.capability_score)}</span></div>
    ${sm.validations ? `<h4 style="margin-top:10px">Held-out Validation</h4>
    <div class="row"><span class="k">Val capability</span><span class="v">${pct(sm.val_capability)} (best ${pct(sm.best_val_capability)})</span></div>
    <div class="row"><span class="k">Val action acc</span><span class="v">${pct(sm.val_action_acc)}</span></div>
    <div class="row"><span class="k">Val drift</span><span class="v">${sm.val_drift ?? "—"}</span></div>
    <div class="row"><span class="k">Validations</span><span class="v">${fmt(sm.validations)}</span></div>` : ""}
    <div class="row"><span class="k">Checkpoint</span><span class="v">${ck.exists ? "● saved" : "—"} · v${fmt(sm.version)} · ${esc(ck.slot || "society_mind")}</span></div>
    <h4 style="margin-top:10px">Population takeover — student drives ${sharePct}%</h4>
    ${takeoverBar(mix)}
    <h4 style="margin-top:10px">Spatial Brain</h4>
    <div class="row"><span class="k">Embodied citizens</span><span class="v">${fmt(sp.positioned)} · ${sp.population_embodiment_pct ?? 0}%</span></div>
    <div class="row"><span class="k">Moving now</span><span class="v">${fmt(sp.moving)}</span></div>
    <div class="row"><span class="k">Action targets</span><span class="v">${dist(sp.target_distribution)}</span></div>
    <div class="row"><span class="k">Actions</span><span class="v">${dist(sp.action_distribution)}</span></div>
    <h4 style="margin-top:10px">Citizen Movement</h4>
    <div class="row"><span class="k">Pathfinding</span><span class="v">${fmt(sp.paths_requested)} paths · ${fmt(sp.failed_path_count)} failed</span></div>
    <div class="row"><span class="k">Average path length</span><span class="v">${sp.avg_path_length ?? 0}</span></div>
    <div class="row"><span class="k">Movement events</span><span class="v">${fmt(sp.movement_events)}</span></div>
    <h4 style="margin-top:10px">Local Perception / Model Inputs</h4>
    <div class="row"><span class="k">Spatial features</span><span class="v">${fmt(sp.feature_count)}</span></div>
    <div class="row"><span class="k">Spatial replay samples</span><span class="v">${fmt(sp.spatial_replay_samples)}</span></div>
    <div class="reason">Disagreement hot spots: ${hotspots(sm.disagreement_hotspots)}</div>
    <h4 style="margin-top:10px">Corpus (training format)</h4>
    <div class="row"><span class="k">Total samples</span><span class="v">${fmt(ds.total)}</span></div>
    <div class="row"><span class="k">Behavior</span><span class="v">${fmt(byCh.behavior)}</span></div>
    <div class="row"><span class="k">Reasoning traces</span><span class="v">${fmt(byCh.reasoning_style)}</span></div>
    <h4 style="margin-top:10px">Teacher — ${esc(tea.model || "27B")}</h4>
    <div class="row"><span class="k">Status</span><span class="v">${tea.online === false ? "⚠ offline" : tea.online ? "● live" : "—"}</span></div>
    <div class="row"><span class="k">Cohorts taught</span><span class="v">${fmt(tea.cohorts_run)}</span></div>
    <div class="row"><span class="k">Citizens taught</span><span class="v">${fmt(tea.citizens_taught)}</span></div>
    <div class="reason">Last cohort: ${esc(tea.last_reason || "—")}</div>
    ${arbiterRows(sm.arbiter || {})}`;
}

function dist(obj) {
  const rows = Object.entries(obj || {}).slice(0, 4)
    .map(([k, v]) => `${esc(k)} ${fmt(v)}`);
  return rows.length ? rows.join(" · ") : "—";
}

function hotspots(obj) {
  if (!obj) return "—";
  return Object.entries(obj).map(([head, vals]) => {
    const top = Object.entries(vals || {})[0];
    return top ? `${head}:${esc(top[0])}` : "";
  }).filter(Boolean).join(" · ") || "—";
}

// The priority LLM scheduler: how the single GPU's scarce calls are shared. Shows the
// protected jobs (governor/teacher/interview) getting slots, plus budget + starvation.
function arbiterRows(a) {
  const labels = a.labels || {};
  const inflight = (a.inflight || []).join(",");
  const order = ["spirit_governor", "cohort_teacher", "citizen_interview", "rare_citizen",
                 "major_event", "world_report", "chronicle", "news", "flavor", "narration"];
  const keys = order.filter(k => labels[k]).concat(
    Object.keys(labels).filter(k => !order.includes(k)));
  const rows = keys.map(k => {
    const v = labels[k];
    const hot = (a.inflight || []).includes(k);
    const extra = [v.skipped ? `${v.skipped} skip` : "", v.deduped ? `${v.deduped} dedup` : "",
                   v.errors ? `${v.errors}✗` : ""].filter(Boolean).join(" · ");
    return `<div class="row"><span class="k">${hot ? "▶ " : ""}${k}</span>
      <span class="v">${fmt(v.calls)}× · ${v.avg_ms}ms${extra ? ` · ${extra}` : ""}</span></div>`;
  }).join("");
  if (!rows) return "";
  const starved = a.most_starved
    ? `${a.most_starved.consumer} (${a.most_starved.waited_s}s)` : "none";
  const budget = a.budget_per_min
    ? `${Math.round(100 * (a.budget_remaining || 0) / a.budget_per_min)}% left` : "—";
  return `<h4 style="margin-top:10px">LLM scheduler (priority economy)</h4>
    <div class="reason">now: ${esc(inflight || "idle")} · queue ${a.queue_depth ?? 0}
      · budget ${budget} · most-starved ${esc(starved)}</div>${rows}`;
}

// A compact inline SVG sparkline of the training loss (newest on the right).
function sparkline(curve) {
  if (!curve || curve.length < 2) return `<div class="reason">Awaiting first training steps…</div>`;
  const w = 240, h = 46, n = curve.length;
  const max = Math.max(...curve), min = Math.min(...curve);
  const span = (max - min) || 1;
  const pts = curve.map((v, i) =>
    `${(i / (n - 1) * w).toFixed(1)},${(h - 4 - (v - min) / span * (h - 8)).toFixed(1)}`).join(" ");
  return `<div style="margin-bottom:8px">
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">
      <polyline points="${pts}" fill="none" stroke="var(--accent-2)" stroke-width="1.6"/>
    </svg>
    <div class="reason" style="display:flex;justify-content:space-between">
      <span>loss ${curve[curve.length - 1]}</span><span>↓ learning</span></div></div>`;
}

// A horizontal stacked bar: who is driving the materialized population right now.
function takeoverBar(mix) {
  const s = mix.student || 0, t = mix.teacher || 0, u = mix.utility || 0;
  const tot = s + t + u || 1;
  const seg = (n, c, label) => n ? `<div title="${label}: ${n}" style="width:${(n / tot * 100).toFixed(1)}%;background:${c}"></div>` : "";
  return `<div style="display:flex;height:16px;border-radius:8px;overflow:hidden;background:var(--bg-2,#222)">
    ${seg(s, "var(--accent-2,#5cf)", "student")}${seg(t, "var(--warn,#f90)", "teacher")}${seg(u, "var(--muted,#556)", "utility")}
    </div>
    <div class="reason" style="display:flex;gap:10px;margin-top:4px">
      <span style="color:var(--accent-2)">● student ${s}</span>
      <span style="color:var(--warn)">● teacher ${t}</span>
      <span style="color:var(--muted)">● utility ${u}</span></div>`;
}

// A thin progress meter with a gate marker (student takes over at the gate).
function meter(val, gate) {
  return `<div style="position:relative;height:6px;border-radius:3px;background:var(--bg-2,#222);margin:2px 0 8px">
    <div style="width:${Math.min(100, val)}%;height:100%;border-radius:3px;background:var(--accent-2,#5cf)"></div>
    <div style="position:absolute;left:${Math.min(100, gate)}%;top:-2px;width:2px;height:10px;background:var(--warn,#f90)"></div></div>`;
}

function fmt(n) { return n == null ? "—" : (+n).toLocaleString(); }
function pct(x) { return x == null ? "—" : `${Math.round(x * 100)}%`; }

function behaviorRows(delta) {
  const entries = Object.entries(delta).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 4);
  if (!entries.length) return "";
  return `<h4 style="margin-top:10px">Behavior Drift</h4>` + entries.map(([k, v]) =>
    `<div class="row"><span class="k">${esc(k)}</span><span class="v" style="color:${v >= 0 ? "var(--accent-2)" : "var(--warn)"}">${v >= 0 ? "+" : ""}${(+v).toFixed(3)}</span></div>`).join("");
}

function set(id, txt) { const el = document.getElementById(id); if (el) el.textContent = txt; }
function esc(s) { return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
