// dashboard.js — the "World" panel: vital signs + world memory (myths & legends).
// Reads live `overview` and `memory` payloads from the store.

import { store } from "./ws.js";

const fmt = (n) => Intl.NumberFormat("en", { notation: "compact" }).format(n);
let unsub = [];
let pendingStats = null;
let statsTimer = 0;
let lastStatsKey = "";

export function render(root) {
  unsub.forEach((f) => f()); unsub = [];
  root.innerHTML = `
    <div class="panel-title">World Overview</div>
    <div class="panel-sub" id="ov-name">A world unfolding…</div>
    <div class="dashboard-grid" id="ov-cards"></div>
    <div class="card" id="ov-stats"></div>
    <div class="panel-title">Observer Influence</div>
    <div class="card" id="observer-card"><div class="empty">No one has heard your voice yet.</div></div>
    <div class="panel-title">World Memory</div>
    <div class="panel-sub">Myths and legends the spirit has woven.</div>
    <div id="ov-myths"><div class="empty">No myths yet. Give it time.</div></div>`;

  unsub.push(store.on("overview", ({ stats }) => queueStats(stats)));
  unsub.push(store.on("memory", (m) => renderMyths(m)));
}

function queueStats(stats) {
  pendingStats = stats;
  if (statsTimer) return;
  statsTimer = setTimeout(flushStats, 350);
}

function flushStats() {
  statsTimer = 0;
  if (!pendingStats) return;
  const stats = pendingStats;
  pendingStats = null;
  const key = statsKey(stats);
  if (key === lastStatsKey) return;
  lastStatsKey = key;
  renderStats(stats);
}

function statsKey(stats) {
  return [
    Math.floor(stats.world_age ?? 0),
    stats.season_index ?? 0,
    stats.year ?? 0,
    stats.population ?? 0,
    stats.city_count ?? 0,
    stats.civilization_count ?? 0,
    stats.largest_city ?? "",
    stats.dominant_civ ?? "",
    stats.unit_count ?? 0,
    stats.famine_count ?? 0,
    stats.war_frequency ?? "",
    Math.round(stats.world_health ?? 0),
    Math.round((stats.biodiversity ?? 0) * 100),
    Math.round((stats.climate_stability ?? 0) * 100),
    (stats.active_events || []).join("|"),
  ].join(";");
}

function rowsFrom(stats) {
  const rows = [
    ["World age", `${fmt(stats.world_age)} ticks`],
    ["Season", `${["🌱","☀️","🍂","❄️"][stats.season_index ?? 0]} ${stats.season || "—"} · Year ${stats.year ?? 0}`],
    ["People (cities)", fmt(stats.population)],
    ["Cities", stats.city_count],
    ["Civilizations", stats.civilization_count],
    ["Largest city", stats.largest_city],
    ["Dominant power", stats.dominant_civ],
    ["Units on the move", stats.unit_count],
    ["Cities in famine", stats.famine_count],
    ["War frequency", stats.war_frequency],
    ["Wildlife", `${fmt(stats.wildlife)} · ${stats.species_count} species`],
    ["Biodiversity", `${stats.biodiversity} (${stats.biodiversity_label})`],
    ["Avg temperature", `${stats.avg_temperature}°C`],
    ["World health", `${Math.round(stats.world_health)}/100`],
    ["Active events", (stats.active_events || []).join(", ") || "none"],
  ];
  return rows.map(([k, v]) =>
    `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`)
    .join("");
}

function renderStats(stats) {
  const el = document.getElementById("ov-stats");
  if (el) el.innerHTML = rowsFrom(stats);
  const cards = document.getElementById("ov-cards");
  if (cards) cards.innerHTML = `
    ${statCard("Health", `${Math.round(stats.world_health)}`, "world vitality", stats.world_health, "#2fd6a8")}
    ${statCard("People", fmt(stats.population), `${stats.city_count} cities`, Math.min(100, stats.population / 200), "#ffcc66")}
    ${statCard("Biodiversity", `${Math.round(stats.biodiversity * 100)}%`, stats.biodiversity_label, stats.biodiversity * 100, "#6ad06b")}
    ${statCard("Climate", `${Math.round(stats.climate_stability * 100)}%`, `${stats.avg_temperature}°C`, stats.climate_stability * 100, "#4ad0ff")}
    ${statCard("Conflict", stats.war_frequency, "last 200 ticks", stats.war_frequency === "high" ? 90 : stats.war_frequency === "moderate" ? 55 : 18, "#ff6b6b")}
    ${statCard("Events", `${(stats.active_events || []).length}`, (stats.active_events || []).join(", ") || "calm", Math.min(100, (stats.active_events || []).length * 24), "#c07bff")}
  `;
}

function statCard(label, value, sub, pct, color) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  return `<div class="stat-card">
    <div class="stat-head"><span>${escape(label)}</span><b>${escape(value)}</b></div>
    <div class="bar-track"><div class="bar" style="width:${p}%;background:${color}"></div></div>
    <div class="tl-detail">${escape(sub)}</div>
  </div>`;
}

function renderMyths(m) {
  renderObserver(m.observer || {});
  const el = document.getElementById("ov-myths");
  if (!el) return;
  if (!m.myths?.length) return;
  el.innerHTML = m.myths.slice().reverse().map((myth) => `
    <div class="myth">
      <h4>${escape(myth.title)}</h4>
      <p>${escape(myth.text)}</p>
    </div>`).join("");
}

function renderObserver(o) {
  const el = document.getElementById("observer-card");
  if (!el) return;
  el.innerHTML = `
    <div class="row"><span class="k">Known as</span><span class="v">${escape(o.persona || "unknown spirit")}</span></div>
    <div class="row"><span class="k">Influence</span><span class="v">${Math.round((o.influence || 0) * 100)}%</span></div>
    <div class="row"><span class="k">Reputation</span><span class="v">${Math.round((o.reputation || 0) * 100)}</span></div>
    ${(o.recent || []).slice().reverse().map((r) =>
      `<div class="reason">Tick ${r.tick}: ${escape(r.effect)}</div>`).join("")}`;
}

function escape(s) {
  return String(s).replace(/[&<>]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
