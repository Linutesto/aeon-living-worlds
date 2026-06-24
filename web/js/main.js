// main.js — bootstrap. Wires the connection, status bar, the 3D world, tab → panel
// routing, map overlays, camera modes, time controls, and the event toast.

import { connect, store, send, api } from "./ws.js";
import { initWorld, setOverlay, setCameraMode, clearFocus } from "./omega/RendererApp.js";
import { initPerfHud } from "./perfhud.js";
import { initTimeControls } from "./timecontrols.js";
import { initFollow, startFollow } from "./follow.js";
import { showToast } from "./toast.js";

import * as worldPanel from "./dashboard.js";
import * as governorPanel from "./governor.js";
import * as timelinePanel from "./timeline.js";
import * as lifePanel from "./inspectors.js";
import * as metricsPanel from "./metrics.js";
import * as godPanel from "./godconsole.js";
import * as setupPanel from "./worldsettings.js";

const PANELS = {
  world: worldPanel, governor: governorPanel, timeline: timelinePanel,
  life: lifePanel, metrics: metricsPanel, god: godPanel, setup: setupPanel,
};

const panelEl = document.getElementById("panel");
const panelBody = document.getElementById("panel-body");
let activeTab = null;
const exitFocusBtn = document.createElement("button");
exitFocusBtn.id = "exit-focus";
exitFocusBtn.className = "exit-focus hidden";
exitFocusBtn.type = "button";
exitFocusBtn.textContent = "Return to map";
document.body.appendChild(exitFocusBtn);

function markGodCameraActive() {
  document.querySelectorAll("#cam-modes .cam")
    .forEach((c) => c.classList.toggle("active", c.dataset.cam === "god"));
}

function returnToMap() {
  clearFocus();
  markGodCameraActive();
}

exitFocusBtn.addEventListener("click", returnToMap);
addEventListener("keydown", (e) => {
  if (e.key === "Escape") returnToMap();
});
addEventListener("focus-enter", () => exitFocusBtn.classList.remove("hidden"));
addEventListener("focus-exit", () => exitFocusBtn.classList.add("hidden"));

function openTab(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  activeTab = name;
  panelBody.innerHTML = "";
  PANELS[name].render(panelBody);
  panelEl.classList.remove("hidden");
}

function selectTab(name) {
  if (name === "world" && activeTab === "world") {     // tap World again => hide
    panelEl.classList.add("hidden"); activeTab = "_map"; return;
  }
  openTab(name);
}

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => selectTab(tab.dataset.tab)));
document.getElementById("panel-grip").addEventListener("click",
  () => panelEl.classList.add("hidden"));

// map overlays
document.querySelectorAll("#map-overlays .chip").forEach((chip) =>
  chip.addEventListener("click", () => {
    document.querySelectorAll("#map-overlays .chip")
      .forEach((c) => c.classList.toggle("active", c === chip));
    setOverlay(chip.dataset.overlay);
  }));

// camera modes
document.querySelectorAll("#cam-modes .cam").forEach((chip) =>
  chip.addEventListener("click", () => {
    document.querySelectorAll("#cam-modes .cam")
      .forEach((c) => c.classList.toggle("active", c === chip));
    setCameraMode(chip.dataset.cam, (speed) => send({ action: "speed", speed }));
  }));

// tapping a city in the 3D world opens its inspector
addEventListener("city-pick", (e) => {
  openTab("life");
  lifePanel.showCity(e.detail.id);
});
addEventListener("building-pick", (e) => {
  openTab("life");
  lifePanel.showBuilding(e.detail.id);
});
addEventListener("person-pick", (e) => {
  // tapping a citizen in the world enters Follow Mode (Phase 9)
  startFollow(e.detail.id);
});
// the dossier's "Follow" button (and Discover person records) can start following too
addEventListener("follow-person", (e) => startFollow(e.detail.id));

// --- status bar ---
const fmt = (n) => Intl.NumberFormat("en", { notation: "compact" }).format(n);
store.on("overview", ({ stats }) => {
  document.getElementById("v-age").textContent = fmt(stats.world_age);
  document.getElementById("v-pop").textContent = fmt(stats.population);
  document.getElementById("v-cities").textContent = stats.city_count;
  document.getElementById("v-civs").textContent = stats.civilization_count;
  document.getElementById("v-war").textContent = stats.war_frequency;
  document.getElementById("v-health").textContent = Math.round(stats.world_health);
});
store.on("_conn", ({ online }) => {
  const el = document.getElementById("conn");
  el.classList.toggle("on", online); el.classList.toggle("off", !online);
});
let fpsQualityMode = "FPS";
store.on("_fps", ({ fps, quality }) => {
  const el = document.getElementById("v-fps");
  if (el) {
    el.textContent = fps;
    el.style.color = fps < 58 ? "var(--warn)" : "var(--accent-2)";
    if (typeof quality === "string") fpsQualityMode = quality;
    const label = el.closest(".vital")?.querySelector("i");
    if (label) label.textContent = fpsQualityMode;
  }
});

// surface dramatic events as a toast regardless of which panel is open
let lastEventId = 0;
setInterval(async () => {
  const { events = [] } = await api("/api/timeline?limit=5");
  if (!Array.isArray(events)) return;
  for (const ev of events.slice().reverse()) {
    if (ev.id > lastEventId) {
      lastEventId = ev.id;
      if (["war", "civilization", "settlement", "event", "collapse", "famine"]
          .includes(ev.type)) showToast(ev.title);
    }
  }
}, 3500);

// --- boot ---
initWorld(document.getElementById("world"));
initPerfHud();             // press P to toggle the renderer performance HUD
initTimeControls();
initFollow((id) => { openTab("life"); lifePanel.showPerson(id); });
connect();
selectTab("world");
