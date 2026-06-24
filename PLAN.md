# AEON — LLM Scheduler + Beautiful 3D Megapass — PLAN

## Restartable / configurable / textured / drift-free pass (2026-06-24) ✅

Made the world **restartable + configurable**, killed **building overlap**, added
**selectable texture packs**, and reworked the **species-mind training** to stop the first
models' behavior drift. (Most of the original wishlist — plural civs, AAA graphics, quality
presets, perf HUD, LOD/instancing — was already built; this pass filled the real gaps.)

- **WorldGenConfig** (`sim/worldgen.py`): one validated bundle of editable gen vars
  (structural ints + `params.BOUNDS` knobs + cosmetic presentation). `create_world(cfg,
  params=)` injects params pre-genesis → **same seed + config ⇒ identical world**.
- **Restart/reset** (`engine.restart` / `restart_random` / `reset_layer`): in-process
  rebuild; minds fresh by default, opt-in keep; layer resets for civ / terrain-climate /
  cities-pop / minds. REST: `/api/world/config[/schema]`, `/api/world/restart[/random]`,
  `/api/world/reset-layer`, `/api/graphics/preset(s)`, `/api/texture-pack(s)`.
- **Placement** (`render/placement.py`): memoized, deterministic, collision-free city
  layout (spatial hash + footprint radii + road clearance + shrink/push/skip). Overlap
  debug overlay via `renderOptions.placementDebug`.
- **Texture packs**: 8 manifests in `web/assets/texturepacks/`; `RendererApp.js` remaps the
  CC0 library live (`setTexturePack`, terrain uniform refresh) + per-pack color grading.
- **Species-mind AWR** (`ai/species_policy.py`): advantage-weighted regression + entropy
  floor + KL trust region replace off-policy REINFORCE; `drift`/`entropy` in `/api/mind`.
- **UI**: new **Setup** tab (`web/js/worldsettings.js`) — New World/Restart, Civilizations,
  Graphics (preset + texture pack + budgets), Debug Placement. Mobile-first.
- **Saves**: v2 carries gen config + preset + pack + budgets + restart metadata; old saves
  load with defaults.
- **Tests**: +39 (`test_species_policy`, `test_restart`, `test_world_api`,
  `test_placement`, `test_save_config`). Full suite green.

## Graphics AAA mega-pass (2026-06-24) ✅

The omega renderer was already mature (per-vertex splat terrain, animated water,
day/night, instancing, LOD, shadow rig, CC0 albedo library, quality presets). This pass
added the three missing AAA layers — **all in `web/js/omega/RendererApp.js`, no new
assets, no fake beauty**:

- **PBR normal maps from albedo.** `deriveNormalMap()` Sobel-derives a tangent-space
  normal map from each loaded CC0 albedo (one-time, 256px offscreen canvas). `attachSurfaceDetail()`
  binds it (matching repeat/wrap) to every building/roof/feature/district/road material, so
  brick/thatch/cobble/cliff/fortress surfaces gain real relief under the sun. Toggle
  `renderOptions.normalMaps`.
- **Image-based lighting (IBL).** `setupEnvironment()` bakes a neutral `RoomEnvironment`
  into a PMREM and sets `scene.environment`, so every `MeshStandardMaterial` gets coherent
  ambient + faint specular (stone sheen, metal roofs, wet water). `envMapIntensity` tuned
  per surface (water 1.1, metal roof 0.9, ground 0.35). Toggle `renderOptions.ibl`.
- **Bloom post-processing.** `setupComposer()` builds `EffectComposer` →
  `RenderPass` → `UnrealBloomPass` (threshold 0.82, so only bright emissive blooms:
  night city-lights, temple/knowledge glows, event beacons) → `OutputPass` (ACES + sRGB).
  `animate()` renders through the composer when bloom is on; gated **off in
  emergency/mobile-low** by `applyQualitySettings`. Toggle `renderOptions.bloom` /
  `bloomStrength`.

Everything degrades gracefully (try/catch around env + composer → falls back to direct
render). Quality presets already cover mobile-low/high/desktop/ultra/rtx-4090-ultra; the
perf HUD (press **P**) shows the cost. Verified: addon imports load from the CDN, no JS
errors, world renders with IBL + normal maps at every tier.

## Civilization overhaul + plural world (2026-06-24) ✅

The world used to read as one homogeneous people that slowly *emerged*; it now **opens
as five distinct rival nations** with real, different characters that live and die.

- **Five civs at genesis.** `sim/civilization.py:seed_initial()` (called from
  `world.create_world`, count = `sim.start_civilizations`, default 5) places five
  distinct-archetype capitals on well-spaced suitable land, each with its own founding
  *people* (a named herbivore lineage) and a founding timeline event. Archetypes live in
  `CIV_ARCHETYPES` (Theocracy / Mercantile Republic / Militarist / Naturalist /
  Technocracy / Nomad / Feudal / Seafaring). The `Civilization` dataclass gained
  `people, color, ideology, ideology_axes, cultural_traits, preferred_desires,
  economic/military/religious/exploration bias, diplomatic_stance, capital_city_id,
  parent_civ_id, status, merged_into, golden_age_tick` (all defaulted; `_ensure_identity`
  repairs old saves and social-layer-built civs).
- **Lifecycle.** `civilization.lifecycle_step()` adds **golden ages**, **mass
  migrations**, voluntary **mergers** of friendly weak neighbours, and **splits** of
  large unstable empires into *successor states* (parent-linked, inherit-then-drift).
  Collapse is enriched; conquest-assimilation stays in `units.py`; city-level revolution
  stays in `society/faction.py` (now inherits parent identity + `parent_civ_id`).
- **Citizens inherit their nation.** `agents/population.py` seeds each person's
  ideology/beliefs/professions from the civ archetype, and adds individuating colour
  (`quirk, speech_style, life_goal, personal_problem, past_event, civ_loyalty,
  class_tension, local_identity` on `Person`). Class distribution fixed (no more
  "everyone is noble"); health/grievance now vary. Dossier + interview prompt use the
  colour so dialogue reflects it.
- **Serialization + UI.** `serialize_cities().civs[]` and the omega chunk city payload
  carry full identity (incl. `color`); new **`political` ("Nations")** overlay tints
  cities by nation. `telemetry/stats.py` + `governor/prompts.py` give the spirit a
  nation roll-call. History filters in `web/js/timeline.js` realigned to every emitted
  type (added golden_age/famine/discovery/extinction/collapse chips + dots).
- **Perf HUD + presets.** Quality preset ladder already existed
  (`omega/QualityGovernor.js`: emergency/mobile-low/mobile-high/desktop/ultra/
  rtx-4090-ultra). Added a toggleable on-screen **perf HUD** (`web/js/perfhud.js`,
  press **P**): fps, render ms, preset/LOD/pixel-ratio, draw calls, triangles,
  meshes/instances, geometries/materials/textures, chunks, JS heap, **sim tick ms**
  (`engine.sim_tick_ms` → governor `perf`), and API latency.
- **Tests.** `tests/test_civilizations.py` (12) covers genesis count/distinctness,
  identity, founding events, capital spacing, citizen inheritance + diversity,
  collapse/merge/split/golden-age, and serialization. **110 tests pass.**

---

# AEON — LLM Scheduler + Beautiful 3D Megapass — PLAN (original)

Living plan for the megapass. **Core rule: no fake beauty** — every visual detail is
derived from real, deterministic simulation state (or deterministically seeded from
world/city/chunk state). LLM calls are a scarce shared resource on one GPU and must be
budgeted intelligently so reports/chronicles never starve the governor, teacher, or
interviews.

## World Perfection Pass — ranked audit

Research references used for this audit:
- **Geometry clipmaps / chunked LOD / CDLOD**: nested terrain rings, crack-free shared
  samples, morphing, and cache-friendly streaming fit AEON's chunked Omega renderer.
- **GPU-driven rendering / Hi-Z occlusion / indirect draws**: let the GPU reject distant
  buildings/vegetation/agents before draw submission, especially for RTX 4090 Ultra.
- **Data-oriented ECS practice**: aggregate components and contiguous scans beat millions
  of Python objects; AEON should keep city-level aggregates and materialize individuals
  only near focus.
- **RimWorld / Dwarf Fortress lesson**: depth is valuable only when readable; every new
  sim system must produce clear pressure, choice, or visual evidence.

Top 25 improvements, ranked by impact per implementation risk:

| # | Improvement | Impact | Cost | Perf cost | Emergence / gameplay value | Order |
|---|---|---:|---:|---:|---:|---|
| 1 | Compact city demographics: age, class, professions, education, urbanization | High | Low | Low | High | ✅ built |
| 2 | Persistent historical sites from real events: ruins, battlefields, shrines, famine scars | High | Low | Low | High | ✅ built |
| 3 | Renderer binds historical sites to terrain scars/landmarks | High | Low | Low | High | ✅ built first slice |
| 4 | Knowledge diffusion through trade/migration/conquest/education | High | Medium | Low | High | Next |
| 5 | District specialization from economy/class/culture | High | Medium | Medium | High | Next |
| 6 | Event-driven dirty propagation for city/render payloads | High | Medium | Low | Medium | Next |
| 7 | Route bundling and selected-only overlays | High | Medium | Low | Medium | Next |
| 8 | GPU instanced impostor cities by archetype/material | High | Medium | Medium | Medium | Renderer |
| 9 | Hierarchical render culling: chunk → district → archetype bins | High | Medium | Low | Medium | Perf |
| 10 | Trade scarcity solver using current stockpiles and road distance | High | Medium | Low | High | Sim |
| 11 | Historical memory influencing diplomacy/religion/unrest | High | Medium | Low | High | Sim |
| 12 | Data-oriented resource/city SoA cache for hot loops | Medium | Medium | Low | Medium | Perf |
| 13 | Night-as-data layer: wealth/knowledge/unrest/culture lights | Medium | Low | Low | Medium | Extend |
| 14 | Biome material resolver with readable terrain pressure colors | Medium | Medium | Medium | Medium | Renderer |
| 15 | Terrain clipmap rings for ultra mode | Medium | High | Medium | Low | Later |
| 16 | Hi-Z / occlusion culling for dense cities | Medium | High | Low GPU / lower CPU | Medium | Later |
| 17 | Ancient roads and abandoned infrastructure decay | Medium | Medium | Low | High | Sim |
| 18 | Diplomacy/culture map overlays from shared memory/religion | Medium | Medium | Low | Medium | Viz |
| 19 | Education and class effects on policy/factions | Medium | Low | Low | High | Next |
| 20 | LLM tier router with hard call classes and fallback templates | Medium | Low | Low | Medium | Scheduler |
| 21 | Compute-driven/worker chunk build queues | Medium | Medium | Low | Low | Perf |
| 22 | Atlas-driven texture/material budget per graphics preset | Medium | Medium | Medium | Low | Renderer |
| 23 | Migration pressure rendering as sparse flow fields | Medium | Medium | Low | Medium | Viz |
| 24 | Disaster memory: plague/famine/war scars alter future settlement appeal | Medium | Medium | Low | High | Sim |
| 25 | Civ identity palettes from culture/religion/resources | Medium | Low | Low | Medium | Renderer |

### Implemented in this pass

- Added city aggregate demographics, class mix, professions, education, urbanization,
  fertility, mortality, and migration pressure in `aeon/sim/cities.py`.
- Added persistent `world.historical_sites` generated only from real events in
  `aeon/sim/world.py`; duplicate event signatures are collapsed and old worlds repair.
- Added compact historical-memory feedback: nearby heritage sites strengthen culture;
  nearby traumatic sites suppress growth and raise unrest through bounded `heritage` and
  `trauma` city fields.
- Exposed demographic payloads through `Engine.serialize_cities()` and `inspect_city()`.
- Exposed persistent historical sites through Omega chunk `scars`; renderer can now show
  durable ruins/battlefields/shrines without inventing map features.
- Added aggregate knowledge diffusion through real contact: city proximity, trade
  infrastructure, migration/trader units, and conflict spread tech domains between civs.
- Added route purpose/importance metadata and district identity profiles so map layers can
  communicate trade, migration, military pressure, class, profession, faith, knowledge,
  industry, trauma, and heritage without debug spam.
- Added regression tests for demographic bounds, shortage → migration pressure,
  historical-site deduplication/feedback, knowledge diffusion, JSON serialization,
  route/district metadata, and render-scar payloads.

## Status legend
✅ done · 🔨 this pass · 📋 planned/staged · ⚠ risk noted

---

## Part 1 — LLM scheduler  🔨 (this pass)

**Why.** One Ollama + one GPU serves the governor, Chronicle, flavor, two narration
workers, and the 27B cohort teacher. Fired concurrently they thrash VRAM and the slow
teacher starves (observed: 1 cohort vs governor tick 28k). v5.1 added a priority
`LLMArbiter`; this pass grows it into a full **scheduler**.

**Design (`aeon/governor/scheduler.py`, evolving `arbiter.py`).** One gate all model
calls funnel through. Single GPU ⇒ `max_concurrent` defaults to 1 (configurable).

- **Consumer classes & priority (lower wins):** `cohort_teacher`(0) ·
  `citizen_interview`(1) · `spirit_governor`(2) · `rare_citizen`(3) ·
  `major_event`(4) · `world_report`(5) ·
  `chronicle`(6) · `news`(7) · `flavor`(8) · `diagnostics`(9).
- **Protected band** (`spirit_governor`, `cohort_teacher`, `citizen_interview`): never
  throttled by budget/quota/cooldown, and aging of lower classes can never cross into it.
- **Token budget:** rolling tokens/min window; when exhausted, low-priority classes get a
  **deterministic fallback template** instead of an LLM call (logged `throttled:budget`).
- **Per-consumer cooldown** (min seconds between calls) and **quota** (max share of the
  recent call window). Exceeded ⇒ fallback/skip for non-protected classes.
- **Starvation aging:** a waiting job's effective priority improves with wait time, capped
  so it never enters the protected band — keeps two low classes from starving each other.
- **Dedup:** identical `cache_key` jobs collapse onto one in-flight future.
- **Stale cancellation:** a job waiting past `max_wait` gives up (fallback, logged
  `skipped:stale`).
- **Rich logging + history ring buffer:** per call record consumer, priority, prompt/out
  size, model, latency, skipped/throttled reason, sim tick, related city/person/faction,
  cache key.

**API:** `/api/mind` ✅, `/api/llm/scheduler` ✅ (live queue + budget + per-consumer
calls/latency/skips/dedup + most-starved + throttle reason), `/api/llm/history` ✅
(recent calls + skips).

**UI:** Spirit-panel "LLM scheduler" card 🔨 — active queue, recent calls, skipped calls,
model usage, budget remaining, average latency, most-starved consumer, throttle reason.

**Tests** (`tests/test_scheduler.py`) ✅: priority ordering; low-priority report delayed;
interview runs under report pressure; governor never starved; duplicate report jobs
collapse; stale jobs cancel; scheduler status/history serialize.

---

## Part 2 — Smart LLM batching  ✅ (cohorts) · 🔨 (tiered routing)

Cohort batching already exists (`aeon/mind/cohort.py` + `teacher.py`): one call per
50–500 citizens, JSON-schema-validated, partial-parse recovery, safe offline fallback,
one training sample per citizen. **This pass:** add **tiered model routing** — a cheap/
fast model for routine cohorts, the 27B only for crisis cohorts / rare citizens / major
cities / player-followed people. Richer cohort grouping (faction/class/desire/religion/
war-exposure/famine/migration) is 📋 (current grouping is city + crisis).

---

## Renderer megapass — living world (16-phase spec)

**Phase 5 — day/night + sim-driven city lights  ✅ (built in active Omega renderer,
`web/js/omega/RendererApp.js`).** A smooth
dawn→noon→dusk→night cycle recolors sun/sky/fog and reveals an emissive **city-lights**
Points cloud whose density+brightness = `wealth × pop × infra × economic_health` — so at
night prosperity is legible "from orbit" (rich metropolis blazes, dead town stays dark).
Knowledge cities tint blue-white, religious cities warm, war/unrest cities red-orange,
plague/famine cities violet/amber. Opacity is driven by dawn/dusk/night factors and
disabled in emergency quality mode.

## Part 3 — 3D visual detail pass  🔨 (first slice built)  ⚠

Deterministic, sim-derived terrain/city/road/citizen/overlay richness. This pass added
resource-state city visuals: famine-dry farms and thin crowd halos; industry darkening,
ash decals, and smoke; knowledge/temple/market light hues; unrest/war torch markers;
roads weighted by wealth/trade dependency/civic stability. Remaining: deeper city
impostors, more district silhouettes, route bundling, and broader texture/atlas work.

## Part 4 — Hidden 3D optimizations  📋 (staged → CODEX.md)  ⚠

InstancedMesh crowds, merged static-chunk geometry, texture/material atlasing, object
pooling, frustum culling, distance LOD, far-building impostors, chunk dirty-flags,
`requestIdleCallback` rebuilds, adaptive render scale, vertex-colors-over-materials,
draw-call reduction, plus a performance HUD (FPS, draw calls, visible chunks, rendered
citizens, LOD, last rebuild, quality-governor state). Staged in CODEX.md.

## Part 5 — Real sim→visual bindings  🔨 (resource bindings built)

City health/famine/wealth/war/religion/technology/trade/resources → concrete deterministic
visual signals. Built fields: per-city production, consumption, shortages, surplus,
demand pressure, trade dependency, famine risk, war readiness, civic stability. These are
serialized through `serialize_cities` and Omega chunk payloads.

## Part 6 — Visual verification  📋

Screenshot matrix (world/city/building zooms × overlay states × mobile/desktop) checking:
no terrain grid squares, no invisible terrain, no overlap regressions, no debug spam, no
FPS collapse, no JS errors. Recipe in `docs/DEVELOPMENT.md`; runs with each renderer pass.

---

## Known risks
- **VRAM (24 GB).** The 27B + KV cache must stay GPU-resident; big cohorts spill to CPU
  (`cohort_size` 60, `teacher_max_tokens` 2560, `keep_alive`). Don't raise blindly.
- **Renderer regressions.** `world3d.js` + `omega/` are load-bearing and shared with
  Codex; the 3D parts are staged, not rushed, per the "don't break the game" rule.
- **Budget tuning.** Token-budget/quotas need live tuning so the world still narrates;
  defaults are conservative and all live-tunable in `config.yaml`.

## Verification (this pass)
`python -m pytest tests/ -q` · `node --check web/js/*.js` · live server with governor +
27B teacher + journaling, polling `/api/llm/scheduler` to confirm: governor/teacher/
interview never starved, reports delayed under pressure, duplicates collapsed, fallbacks
used when budget low; Playwright screenshot of the scheduler panel (Pixel-9 viewport).
