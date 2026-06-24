// inspectors.js — the "Atlas" panel: browse civilizations, cities, PEOPLE, and
// wildlife. People are the headline: pick a city, see its residents, open anyone's
// full dossier (profile, Big Five, goals, skills, kin & rivals, memories, life
// history) and INTERVIEW them — answers come from the local LLM grounded in their
// real state.

import { store, api, post } from "./ws.js";
import { focusBuilding, focusCity, focusCiv, focusPerson } from "./omega/RendererApp.js";

let mode = "people";          // civs | cities | people | wildlife
let detail = null;            // {kind:'city'|'civ'|'species'|'person', id}
let rootEl = null;
let peopleCityId = null;      // which city's residents we're browsing
let peopleQuery = "";
let peopleScope = "city";     // city | all
let peopleAlive = "true";     // true | false | all
let peopleOffset = 0;         // pagination cursor
const PAGE = 60;
const convo = {};             // personId -> [{q,a}]

export function render(root) {
  rootEl = root;
  root.innerHTML = `
    <div class="panel-title">Atlas 🏛</div>
    <div class="filter-chips">
      <button class="chip" data-m="discover">✦ Discover</button>
      <button class="chip" data-m="people">People</button>
      <button class="chip" data-m="cities">Cities</button>
      <button class="chip" data-m="civs">Civilizations</button>
      <button class="chip" data-m="cultures">Cultures</button>
      <button class="chip" data-m="religions">Religions</button>
      <button class="chip" data-m="factions">Factions</button>
      <button class="chip" data-m="wildlife">Wildlife</button>
    </div>
    <div id="atlas-body"></div>`;
  root.querySelectorAll(".chip[data-m]").forEach((c) => {
    c.classList.toggle("active", c.dataset.m === mode);
    c.onclick = () => { mode = c.dataset.m; detail = null; render(root); };
  });
  if (detail) renderDetail();
  else if (mode === "discover") renderDiscover();
  else if (mode === "people") renderPeople();
  else renderList();
}

// Phase 13 — Investigation mode: world records from real simulation state. Each card
// flies the camera to its subject and opens the dossier that explains why it exists.
const DISCO_ICON = {
  largest_city: "🏙", richest_city: "💰", oldest_city: "🏛", famine_hotspot: "🍂",
  oldest_citizen: "👴", richest_citizen: "💎", largest_family: "👨‍👩‍👧‍👦",
  most_aggrieved: "🔥", largest_religion: "⛪", most_influential_faction: "⚔",
  greatest_power: "👑", recent_war: "⚔", great_migration: "🧳",
};
async function renderDiscover() {
  const body = document.getElementById("atlas-body");
  if (!body) return;
  body.innerHTML = `<div class="panel-sub">Records of the world — tap any to investigate.</div>
    <div id="disco-list"><div class="loading">Surveying the world…</div></div>`;
  const data = await api("/api/discoveries");
  const list = document.getElementById("disco-list");
  if (!list) return;
  if (data.error) { list.innerHTML = `<div class="empty">Couldn't survey the world (${esc(data.error)}). <button class="chip" id="disco-retry">Retry</button></div>`;
    document.getElementById("disco-retry").onclick = renderDiscover; return; }
  const recs = data.discoveries || [];
  if (!recs.length) { list.innerHTML = `<div class="empty">The world is young — records emerge as history accrues.</div>`; return; }
  list.innerHTML = recs.map((r, i) => `
    <div class="disco-card" data-i="${i}">
      <div class="disco-ico">${DISCO_ICON[r.key] || "✦"}</div>
      <div style="flex:1">
        <div class="disco-title">${esc(r.title)}</div>
        <div class="disco-subject">${esc(r.subject)}</div>
        <div class="tl-detail">${esc(r.detail || "")}</div>
      </div>
      <div class="disco-go">›</div>
    </div>`).join("");
  list.querySelectorAll(".disco-card").forEach((el) => el.onclick = () => {
    const r = recs[+el.dataset.i];
    investigate(r.focus);
  });
}

// fly the camera to a discovery's subject and open its dossier
function investigate(focus) {
  if (!focus || focus.id == null) return;
  const k = focus.kind;
  if (k === "city") focusCity(focus.id);
  else if (k === "person") focusPerson(focus.id);
  else if (k === "civ") focusCiv(focus.id);
  // open the matching inspector (these explain *why* the subject exists)
  mode = k === "civ" ? "civs" : k === "person" ? "people" : k + "s";
  detail = { kind: k, id: focus.id };
  renderDetail();
}

// called from main.js when a city is tapped in the 3D world
export function showCity(id) {
  mode = "cities"; detail = { kind: "city", id };
  if (rootEl) render(rootEl);
}

export function showPerson(id) {
  mode = "people"; detail = { kind: "person", id };
  focusPerson(id);
  if (rootEl) render(rootEl);
}

export function showBuilding(id) {
  mode = "cities"; detail = { kind: "building", id };
  focusBuilding(id);
  if (rootEl) render(rootEl);
}

// ----------------------------------------------------------------- PEOPLE
// renderPeople builds the toolbar once; loadPeople(append) fetches one page and
// either replaces or appends — so "Load more" never wipes earlier pages.
function renderPeople() {
  const body = document.getElementById("atlas-body");
  if (!body) return;
  const cities = (store.state.cities?.cities || []).slice()
    .sort((a, b) => b.pop - a.pop);
  if (!cities.length) { body.innerHTML = `<div class="empty">No cities yet — people appear once a city is founded.</div>`; return; }
  if (peopleScope === "city" && (peopleCityId == null || !cities.find((c) => c.id === peopleCityId)))
    peopleCityId = cities[0].id;

  body.innerHTML = `
    <div class="people-toolbar">
      <select id="people-city" title="Citizen scope">
        <option value="all" ${peopleScope === "all" ? "selected" : ""}>All materialized citizens</option>
        ${cities.map((c) => `<option value="${c.id}" ${peopleScope === "city" && c.id === peopleCityId ? "selected" : ""}>${esc(c.name)} · ${compact(c.pop)}</option>`).join("")}
      </select>
      <input id="people-search" placeholder="Search citizens, jobs, goals…" value="${esc(peopleQuery)}" />
      <select id="people-alive" title="Living / deceased">
        <option value="true" ${peopleAlive === "true" ? "selected" : ""}>Living</option>
        <option value="false" ${peopleAlive === "false" ? "selected" : ""}>Deceased</option>
        <option value="all" ${peopleAlive === "all" ? "selected" : ""}>All</option>
      </select>
    </div>
    <div class="panel-sub" id="people-sub">Loading citizens…</div>
    <div id="people-list"></div>
    <div id="people-more"></div>`;

  document.getElementById("people-city").onchange = (e) => {
    peopleScope = e.target.value === "all" ? "all" : "city";
    peopleCityId = peopleScope === "city" ? +e.target.value : null;
    peopleOffset = 0; loadPeople(false);
  };
  document.getElementById("people-alive").onchange = (e) => {
    peopleAlive = e.target.value; peopleOffset = 0; loadPeople(false);
  };
  const search = document.getElementById("people-search");
  let searchTimer = null;
  search.oninput = () => {
    peopleQuery = search.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { peopleOffset = 0; loadPeople(false); }, 220);
  };

  peopleOffset = 0;
  loadPeople(false);
}

async function loadPeople(append) {
  const list = document.getElementById("people-list");
  const sub = document.getElementById("people-sub");
  const more = document.getElementById("people-more");
  if (!list) return;
  if (more) more.innerHTML = `<div class="loading">Loading…</div>`;

  // Always the paginated endpoint — city scope focuses to materialize residents.
  const params = new URLSearchParams({
    q: peopleQuery, alive: peopleAlive, limit: String(PAGE), offset: String(peopleOffset),
  });
  if (peopleScope === "city") { params.set("city_id", peopleCityId); params.set("focus", "true"); }
  const data = await api(`/api/people?${params}`);
  if (!document.getElementById("people-list")) return;   // panel changed underneath us
  if (data.error) {
    if (sub) sub.textContent = "";
    if (more) more.innerHTML = "";
    if (!append) list.innerHTML =
      `<div class="empty">Couldn't load citizens (${esc(data.error)}). <button class="chip" id="ppl-retry">Retry</button></div>`;
    const rb = document.getElementById("ppl-retry");
    if (rb) rb.onclick = () => loadPeople(false);
    return;
  }
  const people = data.people || [];
  const cityName = (store.state.cities?.cities || []).find((c) => c.id === peopleCityId)?.name || "the world";
  const shownTo = (data.offset || 0) + people.length;
  if (sub) sub.textContent = `${shownTo} of ${compact(data.count || 0)} ${peopleAlive === "false" ? "remembered dead" : "materialized citizens"} in ${peopleScope === "city" ? cityName : "the world"} · pool ${compact(data.pool?.people || 0)}`;
  if (!people.length && !append) {
    list.innerHTML = `<div class="empty">No citizens match this view.${peopleScope === "city" ? " The city may need a moment to populate." : ""}</div>`;
    if (more) more.innerHTML = ""; return;
  }
  const rows = people.map((p) => `
    <div class="list-item person-row${p.alive === false ? " deceased" : ""}" data-id="${p.id}">
      <span class="swatch" style="background:${classColor(p.social_class)}"></span>
      <div style="flex:1">
        <div>${esc(p.name)}${p.alive === false ? " †" : ""} <span class="tl-detail">· ${p.age}, ${esc(p.profession)}</span></div>
        <div class="tl-detail">${peopleScope === "all" ? `${esc(p.city)} · ` : ""}${esc(p.social_class)} · wants ${esc(p.goal)} · ${esc(p.doing || "idle")}</div>
        <div class="citizen-vitals">
          <span>health ${Math.round((p.health ?? 0) * 100)}%</span>
          <span>status ${Math.round((p.status ?? 0) * 100)}%</span>
          <span class="${(p.grievance ?? 0) > 0.5 ? "hot" : ""}">grievance ${Math.round((p.grievance ?? 0) * 100)}%</span>
        </div>
      </div>
      <div class="row-actions">
        <button class="icon-btn" data-act="open" title="Open dossier">◎</button>
        ${p.alive === false ? "" : `<button class="icon-btn chat" data-act="chat" title="Chat">↗</button>`}
      </div>
    </div>`).join("");
  if (append) list.insertAdjacentHTML("beforeend", rows);
  else list.innerHTML = rows;
  bindPersonRows(list);
  if (more) {
    if (data.has_more) {
      more.innerHTML = `<button class="chip load-more" id="ppl-more">Load ${PAGE} more · ${compact((data.count || 0) - shownTo)} remaining</button>`;
      document.getElementById("ppl-more").onclick = () => { peopleOffset += PAGE; loadPeople(true); };
    } else {
      more.innerHTML = "";
    }
  }
}

// bind click handlers for person rows (idempotent — safe after append)
function bindPersonRows(list) {
  list.querySelectorAll(".person-row:not([data-bound])").forEach((el) => {
    el.setAttribute("data-bound", "1");
    el.onclick = (e) => {
      const action = e.target?.dataset?.act || "open";
      detail = { kind: "person", id: +el.dataset.id };
      renderDetail(action === "chat" ? "chat" : null);
    };
  });
}

function rosterSummary(summary) {
  const classRows = Object.entries(summary.by_class || {})
    .sort((a, b) => b[1] - a[1]).slice(0, 6)
    .map(([k, v]) => `<span>${esc(k)} <b>${v}</b></span>`).join("");
  const workRows = Object.entries(summary.by_profession || {})
    .sort((a, b) => b[1] - a[1]).slice(0, 6)
    .map(([k, v]) => `<span>${esc(k)} <b>${v}</b></span>`).join("");
  const activeRows = Object.entries(summary.active || {})
    .sort((a, b) => b[1] - a[1]).slice(0, 6)
    .map(([k, v]) => `<span>${esc(k)} <b>${v}</b></span>`).join("");
  return `<div class="citizen-summary">
    <div><h4>Classes</h4>${classRows || "<span>none</span>"}</div>
    <div><h4>Work</h4>${workRows || "<span>none</span>"}</div>
    <div><h4>Now</h4>${activeRows || "<span>idle</span>"}</div>
  </div>`;
}

// ----------------------------------------------------------------- LISTS
function listFromStore() {
  const cities = store.state.cities || { cities: [], civs: [] };
  if (mode === "civs") return (cities.civs || []).map((c) => ({
    id: c.id, label: c.name, sub: `${c.ncities} cities · tech ${c.tech}`,
    val: c.pop, color: civHex(c.id), kind: "civ" }));
  if (mode === "cities") return (cities.cities || []).map((c) => ({
    id: c.id, label: c.name, sub: `${c.tier} · ${c.specialty}${c.famine ? " · famine" : ""}`,
    val: c.pop, color: civHex(c.civ), kind: "city" }));
  const soc = store.state.society || { religions: [], factions: [] };
  if (mode === "religions") return (soc.religions || []).map((r) => ({
    id: r.id, label: r.name, sub: `${r.founder} · ${r.cities} cities${r.schism ? " · sect" : ""}`,
    val: r.followers, color: "#ffcf6b", kind: "religion" }));
  if (mode === "cultures") return (soc.cultures || []).map((c) => ({
    id: c.id, label: c.name, sub: `${c.origin} · ${c.value} · ${c.architecture}`,
    val: c.cities, color: "#4ad0ff", kind: "culture" }));
  if (mode === "factions") return (soc.factions || []).map((f) => ({
    id: f.id, label: f.name, sub: `${f.kind.replace("_", " ")} · ${f.seat}`,
    val: Math.round(f.influence * 1000), color: factionColor(f.kind), kind: "faction" }));
  const wl = store.state.wildlife || { species: [] };
  return (wl.species || []).map((s) => ({
    id: s.id, label: s.name, sub: s.diet, val: s.pop,
    color: s.diet === "predator" ? "#ff5a5a" : s.diet === "plant" ? "#4ad06b" : "#ffd24a",
    kind: "species" }));
}

function renderList() {
  const body = document.getElementById("atlas-body");
  if (!body) return;
  const items = listFromStore().sort((a, b) => b.val - a.val).slice(0, 60);
  if (!items.length) { body.innerHTML = `<div class="empty">None yet — give the world time to settle.</div>`; return; }
  const max = Math.max(...items.map((i) => i.val), 1);
  body.innerHTML = items.map((it) => `
    <div class="list-item" data-id="${it.id}" data-kind="${it.kind}">
      <span class="swatch" style="background:${it.color}"></span>
      <div style="flex:1">
        <div>${esc(it.label)} <span class="tl-detail">· ${esc(it.sub)}</span></div>
        <div class="bar-track"><div class="bar" style="width:${(it.val / max) * 100}%;background:${it.color}"></div></div>
      </div>
      <div class="tl-tick">${compact(it.val)}</div>
    </div>`).join("");
  body.querySelectorAll(".list-item").forEach((el) => el.onclick = () => {
    detail = { kind: el.dataset.kind, id: +el.dataset.id }; renderDetail();
  });
}

// ----------------------------------------------------------------- DETAIL
async function renderDetail(openMode = null) {
  const body = document.getElementById("atlas-body");
  if (!body) return;
  body.innerHTML = `<div class="empty">Loading…</div>`;
  if (detail.kind === "person") return renderPerson(body, openMode);
  if (detail.kind === "building") return renderBuilding(body);

  const path = detail.kind === "city" ? `/api/city/${detail.id}`
             : detail.kind === "civ" ? `/api/civ/${detail.id}`
             : detail.kind === "religion" ? `/api/religion/${detail.id}`
             : detail.kind === "culture" ? null
             : detail.kind === "faction" ? `/api/faction/${detail.id}`
             : `/api/species/${detail.id}`;
  if (detail.kind === "culture") {
    const c = (store.state.society?.cultures || []).find((x) => x.id === detail.id);
    if (!c) { body.innerHTML = `<div class="empty">Gone to history.</div>`; return; }
    body.innerHTML = `<button class="chip" id="atlas-back">‹ back</button>
      <div class="panel-title" style="font-size:16px;margin-top:8px">${esc(c.name)}</div>
      <div class="card">
        <div class="row"><span class="k">Origin</span><span class="v">${esc(c.origin)}</span></div>
        <div class="row"><span class="k">Value</span><span class="v">${esc(c.value)}</span></div>
        <div class="row"><span class="k">Architecture</span><span class="v">${esc(c.architecture)}</span></div>
        <div class="row"><span class="k">Cities</span><span class="v">${esc(c.cities)}</span></div>
      </div>`;
    document.getElementById("atlas-back").onclick = () => { detail = null; render(rootEl); };
    return;
  }
  const d = await api(path);
  if (d.error) { body.innerHTML = `<div class="empty">Gone to history.</div>`; return; }

  let rows, extra = "", focusId = null;
  if (detail.kind === "religion") {
    rows = [
      ["Founder", d.founder], ["Holy city", d.holy_city],
      ["Followers", compact(d.followers)], ["Cities of faith", d.cities.length],
      ["Age", `${d.age} ticks`], ["Schism of", d.schism_of || "—"],
    ];
    focusId = d.holy_city_id;
    extra = `<div id="enc-history"></div>
      <div class="card"><h4>Tenets</h4>${d.tenets.map((t) =>
      `<div class="reason">“${esc(t)}”</div>`).join("")}</div>
      <div class="card"><h4>Lands of the faith</h4>${d.cities.slice(0, 12).map((c) =>
      `<div class="row"><span class="k">${esc(c.name)}</span><span class="v">${Math.round(c.share*100)}%</span></div>`).join("")}</div>`;
  } else if (detail.kind === "faction") {
    rows = [
      ["Kind", d.kind.replace("_", " ")], ["Goal", d.goal], ["Founder", d.founder],
      ["Seat", d.seat], ["Influence", `${Math.round(d.influence*100)}%`],
      ["Members", d.member_count], ["Religion", d.religion || "—"],
      ["Age", `${d.age} ticks`],
    ];
    focusId = d.seat_id;
    if (d.members?.length) extra = `<div class="card"><h4>Members</h4>${
      d.members.map((m) => `<div class="row rel" data-id="${m.id}"><span class="k">${esc(m.name)} <i class="tl-detail">${esc(m.profession)}</i></span><span class="v">${m.alive ? "" : "†"}</span></div>`).join("")}</div>`;
  } else if (detail.kind === "city") {
    rows = [
      ["Civilization", d.civ], ["Tier", d.tier], ["Specialty", d.specialty],
      ["Population", compact(d.population)], ["Growth", `${d.growth_rate}%/tick`],
      ["Food production", d.food_production], ["Infrastructure", `${d.infrastructure}/10`],
      ["Culture", d.culture], ["Influence", `${d.influence_radius} tiles`],
      ["Wealth", d.wealth], ["Unrest", d.unrest], ["Age", `${d.age} ticks`],
      ["Status", d.famine ? "FAMINE" : d.plague ? "PLAGUE" : "stable"],
    ];
    focusId = d.id;
    extra = `<div id="enc-history"></div>
      <button class="chip" id="city-people" style="margin-top:8px">👥 View residents</button>
      ${(d.chronicle || []).length ? `<div class="card chronicle-card"><h4>📜 City Chronicle</h4>${
        d.chronicle.map((l) => `<div class="chron-line">${esc(l)}</div>`).join("")}</div>` : ""}
      <div id="city-flavor-card"></div>`;
    if (d.buildings) extra += `<div class="card"><h4>Districts & Buildings</h4>${
      Object.entries(d.buildings).filter(([,v])=>v).map(([k,v]) =>
        `<div class="row"><span class="k">${esc(k.replace("_", " "))}</span><span class="v">${esc(v)}</span></div>`).join("")}</div>`;
    if (d.building_entities?.length) extra += `<div class="card"><h4>Physical Buildings</h4>${
      d.building_entities.slice(0, 12).map((b) =>
        `<div class="row"><span class="k">${esc(b.kind.replace("_", " "))} <i class="tl-detail">${esc(b.district)}${b.abandoned ? " · abandoned" : ""}</i></span><span class="v">${Math.round(b.condition * 100)}% · ${b.workers}</span></div>`).join("")}</div>`;
    if (d.prices) extra += `<div class="card"><h4>Market Prices</h4>${
      Object.entries(d.prices).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([k,v]) =>
        `<div class="row"><span class="k">${esc(k)}</span><span class="v">${(+v).toFixed(2)}</span></div>`).join("")}</div>`;
  } else if (detail.kind === "civ") {
    rows = [
      ["Population", compact(d.population)], ["Cities", d.territory],
      ["Tech level", d.tech], ["Age", `${d.age} ticks`],
      ["Relations", Object.keys(d.relations || {}).length
        ? Object.entries(d.relations).map(([k, v]) => `#${k}:${(+v).toFixed(1)}`).join(", ")
        : "no neighbours"],
    ];
    if (d.tech_domains) extra = `<div class="card"><h4>Technology Domains</h4>${
      Object.entries(d.tech_domains).sort((a,b)=>b[1]-a[1]).map(([k,v]) =>
        `<div class="row"><span class="k">${esc(k)}</span><span class="v">${(+v).toFixed(3)}</span></div>`).join("")}</div>`;
    if (d.cities?.length) extra += `<div class="card"><h4>Cities</h4>${
      d.cities.sort((a, b) => b.pop - a.pop).map((c) =>
        `<div class="row"><span class="k">${esc(c.name)} <i class="tl-detail">${c.tier}</i></span>
         <span class="v">${compact(c.pop)}</span></div>`).join("")}</div>`;
  } else {
    rows = [
      ["Diet", d.diet], ["Population", compact(d.population)], ["Age", `${d.age} ticks`],
      ["Status", d.alive ? "alive" : "extinct"],
      ...Object.entries(d.genome || {}).map(([k, v]) => [`gene: ${k}`, (+v).toFixed(2)]),
    ];
  }

  const canFocus = detail.kind === "civ" || focusId != null;
  body.innerHTML = `
    <button class="chip" id="atlas-back">‹ back</button>
    ${canFocus ? `<button class="chip" id="atlas-focus" style="float:right">◎ Focus camera</button>` : ""}
    <div class="panel-title" style="font-size:16px;margin-top:8px">${esc(d.name)}</div>
    <div class="card">${rows.map(([k, v]) =>
      `<div class="row"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`).join("")}</div>
    ${extra}
    ${(d.history || []).length ? `<div class="card"><h4>Annals</h4>${
      d.history.map((h) => `<div class="reason">${esc(h)}</div>`).join("")}</div>` : ""}`;

  document.getElementById("atlas-back").onclick = () => { detail = null; render(rootEl); };
  const fb = document.getElementById("atlas-focus");
  if (fb) fb.onclick = () => (detail.kind === "civ" ? focusCiv(detail.id) : focusCity(focusId));
  const cp = document.getElementById("city-people");
  if (cp) cp.onclick = () => { mode = "people"; peopleCityId = detail.id; detail = null; render(rootEl); };
  if (detail.kind === "city") { loadCityFlavor(detail.id); loadEntityHistory("city", detail.id); }
  if (detail.kind === "religion") loadEntityHistory("religion", detail.id);
  // member rows (faction) jump to the person
  body.querySelectorAll(".rel[data-id]").forEach((el) => el.onclick = () => {
    detail = { kind: "person", id: +el.dataset.id }; renderDetail();
  });
}

async function renderBuilding(body) {
  const res = await api(`/api/render/entity/building:${encodeURIComponent(detail.id)}`);
  const d = res && res.data;
  if (!d) {
    const msg = res?.error ? `Couldn't load this building (${esc(res.error)}).` : "Building not found.";
    body.innerHTML = `<button class="chip" id="atlas-back">‹ back</button>
      <div class="empty">${msg} ${res?.error ? '<button class="chip" id="bld-retry">Retry</button>' : ""}</div>`;
    document.getElementById("atlas-back").onclick = () => { detail = null; render(rootEl); };
    const rb = document.getElementById("bld-retry");
    if (rb) rb.onclick = () => renderBuilding(body);
    return;
  }
  const inv = Object.entries(d.inventory || {}).sort((a, b) => b[1] - a[1]);
  const prod = Object.entries(d.production || {}).sort((a, b) => b[1] - a[1]);
  body.innerHTML = `
    <button class="chip" id="atlas-back">‹ back</button>
    <button class="chip" id="building-follow" style="float:right">◎ Follow</button>
    <div class="panel-title" style="font-size:16px;margin-top:8px">${esc(d.name || d.id)}</div>
    <div class="panel-sub">${esc(d.city)} · ${esc(d.district)} · ${esc(d.archetype || d.kind)}</div>
    <div class="card">
      <h4>Building</h4>
      <div class="row"><span class="k">Kind</span><span class="v">${esc(d.kind)}</span></div>
      <div class="row"><span class="k">Material</span><span class="v">${esc(d.material || "unknown")}</span></div>
      <div class="row"><span class="k">Condition</span><span class="v">${Math.round((d.condition || 0) * 100)}%</span></div>
      <div class="row"><span class="k">Wealth</span><span class="v">${Math.round((d.wealth || 0) * 100)}%</span></div>
      <div class="row"><span class="k">Age</span><span class="v">${esc(d.age || 0)} ticks</span></div>
      <div class="row"><span class="k">Activity</span><span class="v">${esc(d.activity?.current || "idle")}</span></div>
      <div class="row"><span class="k">Residents</span><span class="v">${d.residents || 0}</span></div>
      <div class="row"><span class="k">Workers</span><span class="v">${d.workers || 0}</span></div>
      <div class="row"><span class="k">Owner</span><span class="v">${d.owner ? esc(d.owner) : "none"}</span></div>
    </div>
    <div class="card"><h4>Inventory</h4>${
      inv.map(([k, v]) => `<div class="row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join("") || "<div class='reason'>No inventory recorded.</div>"}</div>
    <div class="card"><h4>Production</h4>${
      prod.map(([k, v]) => `<div class="row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join("") || "<div class='reason'>No production recorded.</div>"}</div>
    <div class="card"><h4>Influence</h4>
      <div class="row"><span class="k">Religion</span><span class="v">${Math.round((d.influence?.religion || 0) * 100)}%</span></div>
      <div class="row"><span class="k">Faction</span><span class="v">${Math.round((d.influence?.faction || 0) * 100)}%</span></div>
      <div class="row"><span class="k">Rebellion</span><span class="v">${Math.round((d.influence?.rebellion || 0) * 100)}%</span></div>
    </div>
    ${(d.owner_id || d.worker_ids?.length || d.resident_ids?.length) ? `<div class="card"><h4>People</h4>
      ${d.owner_id ? `<div class="row rel" data-id="${d.owner_id}"><span class="k">Owner</span><span class="v">${esc(d.owner || d.owner_id)}</span></div>` : ""}
      ${(d.worker_ids || []).slice(0, 8).map((id) => `<div class="row rel" data-id="${id}"><span class="k">Worker</span><span class="v">#${id}</span></div>`).join("")}
      ${(d.resident_ids || []).slice(0, 8).map((id) => `<div class="row rel" data-id="${id}"><span class="k">Resident</span><span class="v">#${id}</span></div>`).join("")}
    </div>` : ""}
    ${(d.history || []).length ? `<div class="card"><h4>History</h4>${
      d.history.map((h) => `<div class="reason">${esc(h)}</div>`).join("")}</div>` : ""}`;

  document.getElementById("atlas-back").onclick = () => { detail = null; render(rootEl); };
  document.getElementById("building-follow").onclick = () => focusBuilding(d.id);
  body.querySelectorAll(".rel[data-id]").forEach((el) => el.onclick = () => {
    detail = { kind: "person", id: +el.dataset.id }; renderDetail();
  });
}

// ----------------------------------------------------------------- PERSON
const QUESTIONS = ["Who are you?", "Why did you leave your city?",
  "What do you think about the war?", "Who are your enemies?",
  "What do you hope for?", "Tell me about your family."];

async function renderPerson(body, openMode = null) {
  const d = await api(`/api/person/${detail.id}`);
  if (d.error) { body.innerHTML = `<button class="chip" id="atlas-back">‹ back</button><div class="empty">This person is no longer remembered.</div>`;
    document.getElementById("atlas-back").onclick = () => { detail = null; render(rootEl); }; return; }

  const trait = (k) => Math.round((d.personality[k] || 0) * 100);
  const bars = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]
    .map((k) => `<div class="trait"><span>${k.slice(0,4)}</span>
      <div class="bar-track"><div class="bar" style="width:${trait(k)}%"></div></div></div>`).join("");
  const rels = d.relationships.map((r) => `
    <div class="row rel" data-id="${r.id}">
      <span class="k">${esc(r.name)} <i class="tl-detail">${r.kind}${r.note ? " · " + esc(r.note) : ""}</i></span>
      <span class="v" style="color:${r.strength >= 0 ? "#4ad06b" : "#ff6b6b"}">${r.strength >= 0 ? "+" : ""}${r.strength}</span>
    </div>`).join("") || `<div class="reason">Keeps to themselves.</div>`;

  const interviewCard = d.alive ? `
    <div class="card">
      <h4>Interview</h4>
      <div id="convo"></div>
      <div class="filter-chips" id="q-presets">${QUESTIONS.map((q) =>
        `<button class="chip qq">${esc(q)}</button>`).join("")}</div>
      <div class="ask-row">
        <input id="ask-input" placeholder="Ask ${esc(d.name.split(" ")[0])} anything…" />
        <button id="ask-send" class="god-btn" style="min-height:40px;width:64px">Ask</button>
      </div>
    </div>` : deceasedCard(d);

  body.innerHTML = `
    <button class="chip" id="atlas-back">‹ back</button>
    ${d.alive ? `<button class="chip follow-btn" id="follow-btn" style="float:right">🎥 Follow</button>` : ""}
    ${d.home_city_id != null ? `<button class="chip" id="goto-city" style="float:right">◎ ${esc(d.home_city)}</button>` : ""}
    <div class="panel-title" style="font-size:16px;margin-top:8px">${esc(d.name)}${d.alive ? "" : " †"}</div>
    <div class="panel-sub">${d.alive
      ? esc(d.summary) + " · " + esc(d.doing || "")
      : `<span class="deceased-tag">Deceased</span> ${esc(d.summary)} · died of ${esc(d.death_cause || "unknown")}`}</div>

    ${interviewCard}

    <div class="card"><h4>Personality</h4>${bars}</div>
    <div class="card"><h4>Faith & Allegiance</h4>
      <div class="row"><span class="k">Religion</span><span class="v">${d.religion ? esc(d.religion) : "none"}</span></div>
      <div class="row"><span class="k">Factions</span><span class="v" style="text-align:right">${
        (d.factions||[]).map((f)=>esc(f.name)).join("<br>") || "none"}</span></div>
      <div class="row"><span class="k">Grievance</span><span class="v" style="color:${d.grievance>0.5?"#ff6b6b":"var(--fg)"}">${Math.round((d.grievance||0)*100)}%</span></div>
    </div>
    <div class="card"><h4>Inner Life</h4>
      ${mindRow(d)}
      ${d.emotion ? `<div class="row"><span class="k">Feeling</span><span class="v">${esc(d.emotion)}${d.intent ? ` · ${esc(d.intent)}` : ""}</span></div>` : ""}
      ${d.last_dialogue ? `<div class="reason" style="font-style:italic">“${esc(d.last_dialogue)}”</div>` : ""}
      <div class="row"><span class="k">Mood</span><span class="v">${Math.round((d.mood || 0) * 100)}</span></div>
      <div class="row"><span class="k">Stress</span><span class="v">${Math.round((d.stress || 0) * 100)}%</span></div>
      <div class="row"><span class="k">Trusts observer</span><span class="v">${Math.round((d.trust_observer || 0) * 100)}%</span></div>
      <div class="row"><span class="k">Home</span><span class="v">${esc(d.home_building || "home")}</span></div>
      <div class="row"><span class="k">Work</span><span class="v">${esc(d.work_building || "work")}</span></div>
      <div class="row"><span class="k">Ambitions</span><span class="v" style="text-align:right">${(d.ambitions || []).map(esc).join("<br>") || "—"}</span></div>
    </div>
    <div class="card"><h4>Drives</h4>
      <div class="row"><span class="k">Chief goal</span><span class="v">${esc(d.dominant_goal)}</span></div>
      <div class="row"><span class="k">Beliefs</span><span class="v" style="text-align:right">${d.beliefs.map(esc).join("<br>") || "—"}</span></div>
      <div class="row"><span class="k">Fears</span><span class="v">${d.fears.map(esc).join(", ") || "—"}</span></div>
      <div class="row"><span class="k">Loves</span><span class="v">${d.preferences.map(esc).join(", ") || "—"}</span></div>
      <div class="row"><span class="k">Skills</span><span class="v" style="text-align:right">${
        Object.entries(d.skills).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([k,v])=>`${k} ${Math.round(v*100)}`).join("<br>") || "—"}</span></div>
    </div>
    <div class="card"><h4>Plans & Rumors</h4>
      ${(d.active_plans || []).map((p)=>`<div class="reason">Plan: ${esc(p.kind)} · ${esc(p.source || "self")} · ${p.progress || 0}/${p.duration || "?"}</div>`).join("") || "<div class='reason'>No active plans.</div>"}
      ${(d.rumors || []).slice().reverse().map((r)=>`<div class="reason">Rumor: ${esc(r)}</div>`).join("")}
    </div>
    <div class="card"><h4>Possessions</h4>${
      Object.entries(d.possessions || {}).map(([k,v])=>`<div class="row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join("") || "<div class='reason'>Nothing of note.</div>"}</div>
    <div class="card"><h4>Relationships</h4>${rels}</div>
    <div class="card" id="bio-card"><h4>📖 Biography</h4>
      <div id="bio-text" class="reason">The chronicler can interpret this life.</div>
      <button class="chip" id="bio-btn" style="margin-top:6px">✒ Write biography</button></div>
    <div class="card" id="family-card"><h4>🌳 Family</h4><div class="loading">…</div></div>
    <div class="card"><h4>📜 Life Chronicle</h4>${
      (d.life_chronicle || []).length
        ? d.life_chronicle.map((ev) => `<div class="life-ev">
            <span class="life-ico">${ev.icon || "•"}</span>
            <span class="life-text">${esc(ev.text)}</span></div>`).join("")
        : "<div class='reason'>A quiet life, little recorded.</div>"}</div>
    <div class="card"><h4>What they remember</h4>${
      d.memories.map((m)=>`<div class="reason" style="color:${m.valence<-0.3?"#ff8a8a":m.valence>0.3?"#9be7a0":"var(--muted)"}">• ${esc(m.text)}</div>`).join("")}</div>`;

  document.getElementById("atlas-back").onclick = () => {
    detail = null; render(rootEl);
  };
  const gc = document.getElementById("goto-city");
  if (gc) gc.onclick = () => focusCity(d.home_city_id);
  const followBtn = document.getElementById("follow-btn");
  if (followBtn) followBtn.onclick = () =>
    dispatchEvent(new CustomEvent("follow-person", { detail: { id: d.id } }));
  loadFamily(d.id);
  const bioBtn = document.getElementById("bio-btn");
  if (bioBtn) bioBtn.onclick = async () => {
    const out = document.getElementById("bio-text");
    bioBtn.disabled = true; out.innerHTML = `<span class="loading">The chronicler writes</span>`;
    const r = await api(`/api/person/${d.id}/biography`, 60000);
    out.textContent = r.biography || r.error || "(the words would not come)";
    bioBtn.textContent = r.cached ? "✒ Rewrite" : "✒ Rewrite biography";
    bioBtn.disabled = false;
  };
  body.querySelectorAll(".rel, .rel-pill").forEach((el) => el.onclick = () => {
    detail = { kind: "person", id: +el.dataset.id }; renderDetail();
  });

  // interview wiring — only for the living; the dead show an archive instead
  const askSend = document.getElementById("ask-send");
  if (d.alive && askSend) {
    const log = document.getElementById("convo");
    const renderConvo = () => {
      log.innerHTML = (convo[d.id] || []).map((t) => `
        <div class="q">“${esc(t.q)}”</div>
        <div class="a">${t.a === null ? "<i>…thinking…</i>" : esc(t.a)}
          ${t.c ? `<div class="consequence">${esc((t.c.effects || []).join(", ") || "remembered")} ${t.c.planned ? "· plan: " + esc(t.c.planned) : ""}</div>` : ""}
        </div>`).join("");
      log.scrollTop = log.scrollHeight;
    };
    renderConvo();
    const askQ = async (q) => {
      if (!q) return;
      (convo[d.id] = convo[d.id] || []).push({ q, a: null });
      renderConvo();
      const res = await post(`/api/person/${d.id}/ask`, { question: q });
      const last = convo[d.id][convo[d.id].length - 1];
      last.a = res.answer || res.error || "(silence)";
      last.c = res.consequence || null;
      renderConvo();
    };
    askSend.onclick = () => {
      const inp = document.getElementById("ask-input");
      askQ(inp.value.trim()); inp.value = "";
    };
    document.getElementById("ask-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") askSend.click();
    });
    body.querySelectorAll(".qq").forEach((b) => b.onclick = () => askQ(b.textContent));
    if (openMode === "chat") {
      setTimeout(() => document.getElementById("ask-input")?.focus(), 40);
    }
  }
}

// async, cached world flavor (rumors, news, sermons…) for a city
async function loadCityFlavor(cityId) {
  const host = document.getElementById("city-flavor-card");
  if (!host) return;
  const data = await api(`/api/flavor?city_id=${cityId}`);
  if (!document.getElementById("city-flavor-card")) return;
  const pieces = (data && data.pieces) || [];
  if (!pieces.length) {
    host.innerHTML = `<div class="card"><h4>Word on the Street</h4>
      <div class="reason">No tales yet — they gather as the world turns.</div></div>`;
    return;
  }
  host.innerHTML = `<div class="card"><h4>Word on the Street</h4>${
    pieces.map((p) => `<div class="flavor-piece">
      <span class="flavor-kind">${esc(p.kind)}</span>
      <span class="flavor-text">${esc(p.text)}</span></div>`).join("")}</div>`;
}

// Encyclopedia — an LLM-written, grounded history for a city or religion (cached).
async function loadEntityHistory(kind, id) {
  const host = document.getElementById("enc-history");
  if (!host) return;
  host.innerHTML = `<div class="card enc-card"><h4>📖 ${kind === "city" ? "City History" : "History of the Faith"}</h4>
    <div id="enc-text" class="enc-text"><span class="reason">An entry awaits the chronicler.</span></div>
    <button class="chip" id="enc-btn">✒ Write history</button></div>`;
  document.getElementById("enc-btn").onclick = async () => {
    const btn = document.getElementById("enc-btn");
    const out = document.getElementById("enc-text");
    btn.disabled = true; out.innerHTML = `<span class="loading">The chronicler writes</span>`;
    const r = await api(`/api/${kind}/${id}/history`, 60000);
    out.textContent = r.history || r.error || "(no history could be set down)";
    btn.textContent = "✒ Rewrite"; btn.disabled = false;
  };
}

// Phase 2 — explorable family tree (lazy; tap any relative to jump to them)
async function loadFamily(pid) {
  const host = document.getElementById("family-card");
  if (!host) return;
  const f = await api(`/api/person/${pid}/family`);
  if (!document.getElementById("family-card")) return;
  if (f.error) { host.innerHTML = `<h4>🌳 Family</h4><div class="reason">Unknown lineage.</div>`; return; }
  const chip = (n) => n
    ? `<span class="kin-chip ${n.alive ? "" : "dead"}" data-id="${n.id}">${esc(n.name)}${n.alive ? "" : " †"}${n.profession ? ` <i>${esc(n.profession)}</i>` : ""}</span>`
    : "";
  const row = (label, nodes) => nodes && nodes.length
    ? `<div class="kin-row"><span class="kin-label">${label}</span><span>${nodes.map(chip).join("")}</span></div>` : "";
  host.innerHTML = `<h4>🌳 House ${esc(f.dynasty)}</h4>
    ${row("Parents", f.parents)}
    ${f.spouse ? row("Spouse", [f.spouse]) : ""}
    ${row("Siblings", f.siblings)}
    ${row("Children", f.children)}
    <div class="row"><span class="k">Family influence</span><span class="v">${Math.round((f.family_influence || 0) * 100)}%</span></div>
    ${(!f.parents.length && !f.spouse && !f.children.length) ? "<div class='reason'>No known kin.</div>" : ""}`;
  host.querySelectorAll(".kin-chip[data-id]").forEach((el) => el.onclick = () => {
    detail = { kind: "person", id: +el.dataset.id }; renderDetail();
  });
}

// the archive panel shown in place of the interview for a deceased citizen
function deceasedCard(d) {
  const a = d.archive || {};
  const legacy = a.legacy || {};
  const founded = [
    ...(legacy.founded_religions || []).map((r) => `<span class="legacy-pill">⛪ ${esc(r.name)}</span>`),
    ...(legacy.founded_factions || []).map((f) => `<span class="legacy-pill">⚔ ${esc(f.name)}</span>`),
  ].join("");
  const descendants = (legacy.descendants || [])
    .map((c) => `<span class="rel-pill" data-id="${c.id}">${esc(c.name)}${c.alive ? "" : " †"}</span>`).join("");
  const quotes = (a.quotes || []).map((q) => `<div class="reason">“${esc(q)}”</div>`).join("")
    || "<div class='reason'>No words of theirs survive.</div>";
  return `
    <div class="card deceased-card">
      <h4>⚰ In Memoriam</h4>
      <div class="reason">The dead keep their silence. ${esc(d.name)} lives on only in the
        world's memory — their deeds, their words, and the lives they shaped.</div>
      <div class="row"><span class="k">Died of</span><span class="v">${esc(a.death_cause || d.death_cause || "unknown")}</span></div>
      ${a.lifespan != null ? `<div class="row"><span class="k">Lifespan</span><span class="v">${a.lifespan} ticks</span></div>` : ""}
      ${founded ? `<div class="legacy-row"><b>Legacy:</b> ${founded}</div>` : ""}
      ${descendants ? `<div class="legacy-row"><b>Descendants:</b> ${descendants}</div>` : ""}
    </div>
    <div class="card"><h4>Remembered words</h4>${quotes}</div>`;
}

// keep open lists fresh
store.on("cities", () => {
  if (rootEl && !detail && (mode === "cities" || mode === "civs")) renderList();
});
store.on("society", () => {
  if (rootEl && !detail && (mode === "religions" || mode === "factions" || mode === "cultures")) renderList();
});
store.on("wildlife", () => { if (rootEl && !detail && mode === "wildlife") renderList(); });

function factionColor(kind) {
  return { merchant_league: "#ffd24a", guild: "#e0a85a", religious_order: "#ffcf6b",
    military_order: "#ff6b6b", secret_society: "#9b8cff", revolutionary: "#ff4a4a",
    political_party: "#4ad0ff" }[kind] || "#b89cff";
}
function classColor(cls) {
  return { destitute: "#7a7a8a", commoner: "#9aa0b0", freeholder: "#6aa84f",
    merchant: "#ffd24a", gentry: "#c07bff", noble: "#ff8a3b" }[cls] || "#9aa0b0";
}
// which cognition is driving this person right now — the Society Intelligence Stack
function mindRow(d) {
  const src = d.mind_source || "utility";
  const c = { student: "var(--accent-2,#5cf)", teacher: "var(--warn,#f90)",
              utility: "var(--muted,#88a)" }[src] || "var(--muted,#88a)";
  const label = { student: "Liquid student 🧠", teacher: "27B teacher",
                  utility: "Utility model" }[src] || src;
  return `<div class="row"><span class="k">Driven by</span>
    <span class="v" style="color:${c}">${label}</span></div>`;
}

function civHex(id) { return `hsl(${(id * 67) % 360} 60% 58%)`; }
function compact(n) { return Intl.NumberFormat("en", { notation: "compact" }).format(n); }
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
}[c])); }
