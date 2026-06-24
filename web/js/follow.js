// follow.js — Phase 9 Citizen Follow Mode.
// When a citizen is followed, the camera tracks them and a HUD shows who they are and
// what they are doing *right now*, polled live from the simulation. Everything shown is
// real state (name/age/profession/culture/religion/family/activity) — no invention.

import { api } from "./ws.js";
import { focusPerson, focusCity } from "./omega/RendererApp.js";

let followId = null;
let timer = null;
let onOpenStory = null;       // callback to open the full Life Chronicle dossier

export function initFollow(openStory) {
  onOpenStory = openStory;
  document.getElementById("fh-close").onclick = stopFollow;
  document.getElementById("fh-story").onclick = () => {
    if (followId != null && onOpenStory) onOpenStory(followId);
  };
  document.getElementById("fh-home").onclick = () => {
    const cid = Number(document.getElementById("follow-hud").dataset.cityId);
    if (cid) focusCity(cid);
  };
}

export function startFollow(id) {
  followId = id;
  focusPerson(id);                                  // camera tracks them
  document.getElementById("follow-hud").classList.remove("hidden");
  refresh();
  clearInterval(timer);
  timer = setInterval(refresh, 2200);               // live, cheap poll
}

export function stopFollow() {
  followId = null;
  clearInterval(timer);
  timer = null;
  document.getElementById("follow-hud").classList.add("hidden");
}

export function isFollowing() { return followId != null; }

async function refresh() {
  if (followId == null) return;
  const d = await api(`/api/person/${followId}/live`);
  const hud = document.getElementById("follow-hud");
  if (!hud || followId == null) return;
  if (d.error) { document.getElementById("fh-activity").textContent = "(lost from sight)"; return; }
  hud.dataset.cityId = d.city_id ?? "";
  document.getElementById("fh-name").textContent = d.name + (d.alive ? "" : " †");
  document.getElementById("fh-sub").textContent =
    `${d.age}, ${d.profession} · ${d.social_class} · ${d.city}`;
  const clock = d.hour != null ? `${String(d.hour).padStart(2, "0")}:00 ${d.time_of_day} · ${d.season || ""}` : "";
  document.getElementById("fh-activity").innerHTML = d.alive
    ? `<span class="fh-clock">${esc(clock)}</span> Now: <b>${esc(d.activity)}</b>`
      + (d.next_activity ? ` <span class="fh-next">→ then ${esc(d.next_activity)}</span>` : "")
      + (d.why ? `<div class="fh-why">${esc(d.why)}</div>` : "")
    : `Died — ${esc(d.activity)}`;
  const moodPct = Math.round(((d.mood ?? 0) + 1) * 50);
  const fam = d.kin?.length
    ? d.kin.map((k) => `${k.name} (${k.rel})`).join(", ") : "no close kin";
  document.getElementById("fh-grid").innerHTML = `
    <div><b>${d.religion ? esc(d.religion) : "—"}</b><span>faith</span></div>
    <div><b>${d.faction_count || 0}</b><span>factions</span></div>
    <div><b>${Math.round((d.health ?? 0) * 100)}%</b><span>health</span></div>
    <div><b>${moodPct}%</b><span>mood</span></div>
    <div class="fh-wide"><b>${esc(fam)}</b><span>family</span></div>`;
}

function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c])); }
