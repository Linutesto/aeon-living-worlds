// timeline.js — the "History" panel: a scrollable, filterable chronicle.
// Pulls from REST (/api/timeline) so it can show deep history, and refreshes
// while open.

import { api } from "./ws.js";

// Filter chips, ordered roughly by drama. Every type here is actually emitted by the
// sim/society layers (see aeon/**/_ev + history.extend), so no tab is dead.
const TYPES = ["all", "civilization", "collapse", "golden_age", "schism",
               "revolution", "holy_war", "war", "religion_founded", "faction_founded",
               "migration", "famine", "discovery", "economy", "culture",
               "extinction", "settlement", "death", "observer", "governor"];
const DOT = {
  event: "#ffcc66", war: "#ff6b6b", civilization: "#7c5cff", settlement: "#b89cff",
  speciation: "#2fd6a8", extinction: "#888", collapse: "#ff8a5a", governor: "#5cc8ff",
  event_end: "#665", religion_founded: "#ffcf6b", schism: "#ffa94a",
  faction_founded: "#9b8cff", revolution: "#ff3b3b", holy_war: "#ff6b3b",
  birth: "#7be0a0", death: "#888", social: "#5cc8ff", migration: "#4ad0ff",
  economy: "#ffcc66", culture: "#4ad0ff", observer: "#2fd6a8",
  golden_age: "#ffd700", famine: "#d98a3c", discovery: "#6be0ff", trade: "#cfe06b",
  rumor: "#b0a0d0",
};
let filter = "all";
let view = "events";       // events | chronicle
let timer = null;

export function render(root) {
  root.innerHTML = `
    <div class="panel-title">World History 📜</div>
    <div class="filter-chips" id="tl-mode">
      <button class="chip ${view === "events" ? "active" : ""}" data-v="events">Timeline</button>
      <button class="chip ${view === "chronicle" ? "active" : ""}" data-v="chronicle">Chronicle</button>
      <button class="chip ${view === "news" ? "active" : ""}" data-v="news">📰 News</button>
    </div>
    <div class="filter-chips" id="tl-filters"></div>
    <div id="tl-list"><div class="empty">Loading…</div></div>`;

  document.querySelectorAll("#tl-mode .chip").forEach((c) =>
    c.addEventListener("click", () => { view = c.dataset.v; render(root); }));

  const chips = document.getElementById("tl-filters");
  if (view === "events") {
    chips.innerHTML = TYPES.map((t) =>
      `<button class="chip ${t === filter ? "active" : ""}" data-t="${t}">${t.replace("_", " ")}</button>`).join("");
    chips.querySelectorAll(".chip").forEach((c) =>
      c.addEventListener("click", () => {
        filter = c.dataset.t;
        chips.querySelectorAll(".chip").forEach((x) => x.classList.toggle("active", x === c));
        refresh();
      }));
  } else {
    chips.innerHTML = `<span class="panel-sub">The world's history, set down by the chronicler.</span>`;
  }

  refresh();
  clearInterval(timer);
  timer = setInterval(refresh, 5000);
}

async function refresh() {
  const list = document.getElementById("tl-list");
  if (!list) { clearInterval(timer); return; }
  if (view === "chronicle") return refreshChronicle(list);
  if (view === "news") return refreshNews(list);
  const q = filter === "all" ? "" : `?type=${filter}`;
  const { events } = await api(`/api/timeline${q}`);
  if (!events.length) { list.innerHTML = `<div class="empty">Nothing here yet.</div>`; return; }
  list.innerHTML = events.map((e) => `
    <div class="tl-item">
      <div class="tl-dot" style="background:${DOT[e.type] || "#777"}"></div>
      <div class="tl-body">
        <div class="tl-title">${esc(e.title)}</div>
        <div class="tl-detail">${esc(e.detail || "")}</div>
        ${e.why ? `<div class="why">Why: ${esc(whyText(e.why))}</div>` : ""}
      </div>
      <div class="tl-tick">${e.tick}</div>
    </div>`).join("");
}

async function refreshNews(list) {
  if (!list.dataset.loaded) list.innerHTML = `<div class="loading">The presses are running</div>`;
  const data = await api("/api/newspaper", 60000);
  if (!document.getElementById("tl-list")) return;
  list.dataset.loaded = "1";
  if (!data.items) { list.innerHTML = `<div class="empty">No news yet — history must first be made.</div>`; return; }
  list.innerHTML = `<div class="news-sheet">
    <div class="news-masthead">THE WORLD REPORT</div>
    <div class="news-body">${esc(data.items).replace(/\n/g, "<br>")}</div></div>`;
}

async function refreshChronicle(list) {
  const { entries } = await api("/api/chronicle");
  if (!entries || !entries.length) {
    list.innerHTML = `<div class="empty">The chronicler has yet to set down a great event.<br>Watch for a religion, a revolution, or a war.</div>`;
    return;
  }
  list.innerHTML = entries.map((e) => `
    <div class="chron">
      <div class="chron-title">${esc(e.title)}</div>
      <div class="chron-tick">— the ${1 + Math.floor(e.tick / 1000)}th age, year ${e.tick}</div>
      <div class="chron-text">${esc(e.text)}</div>
    </div>`).join("");
}

function whyText(why) {
  return Object.entries(why).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
}
function esc(s) { return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
