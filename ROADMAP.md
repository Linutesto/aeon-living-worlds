# AEON — Roadmap

The north star: a persistent synthetic civilization where every large-scale historical
event emerges from individual decisions, and every individual is influenced by
large-scale forces — observable from planetary scale down to a single life, on a phone.

## Shipped

- **Phase 1 — Foundation.** Deterministic world (terrain, climate, resources, species,
  evolution), the world-spirit LLM governor (clamped directives, myths, goals), FastAPI +
  WebSocket, mobile dashboard scaffold.
- **Phase 2 — Civilization renderer.** Real cities that emerge/grow/expand, moving units
  (traders, caravans, migrants, explorers, armies), visible events, the Three.js renderer
  with 5 camera modes + territory/trade overlays, smooth 60 fps via client interpolation.
- **Phase 3 — Individuals.** The LOD persona pool; persons with Big-Five personality,
  decaying memory, relationships, goals, skills; grounded LLM **interviews**; per-species
  **PyTorch (GPU) learning policies** with a numpy fallback.
- **Phase 4 — Emergent society + Chronicle.** Ideology + grievance; **religions** (found,
  spread, convert, schism, holy war); **factions** (guilds/leagues/orders/revolutions)
  with influence feedback (revolutions birth new civilizations); the event-driven LLM
  **Chronicle**; Atlas "follow anything" browsers.
- **Release-candidate passes (shipped).** Multi-resource economy + trade; the Omega
  chunk-streamed renderer (`web/js/omega/`); real **CC0 textures** (terrain splat +
  building walls/roofs, `docs/ASSET_LICENSES.md`); composite roofed buildings; **seasons**
  (food/travel/vegetation factors, year cycle); **citizen follow mode** + **daily
  schedules** (`agents/schedule.py`); **life chronicles**, **family trees + inheritance**;
  **discovery/investigation** Atlas; save/load + autosave; and the **LLM interpretation
  layer** (`society/interpret.py` + `engine._narrate`): grounded, cached **biographies**,
  **city/religion histories**, and the **Daily World Report** newspaper. 53 tests pass.

- **Phase 5 — Society Intelligence Stack (shipped).** A live teacher→student
  distillation loop inside AEON (`aeon/mind/`). A **27B teacher**
  (`vaultbox/qwen3.5-uncensored:27b`) reasons over whole **cohorts** of citizens in one
  call (crisis cities first; never per-agent), enriching each person's inner life
  (emotion/memory/intent/dialogue) advisorily while the deterministic life-tick stays
  authoritative. Every output is logged in the canonical training format
  (`SocietyDataset`, JSONL). A real **liquid CfC** net (`LiquidSocietyNet`, pure-torch
  closed-form continuous-time cells, multi-head) — the **student** — trains continuously
  on the GPU (`SocietyTrainer`, double-buffered serving/training copies) and, as its
  agreement with the teacher rises past a gate, **progressively takes over** the
  population's per-tick cognition (`HybridMind`). Filtered external reasoning
  traces seed a separate `reasoning_style` channel (`TraceIngester`). The Spirit panel's
  **Society Mind (Level 3)** card visualizes it live: loss sparkline, teacher agreement,
  GPU "sweat", corpus growth, and the **student/teacher/utility takeover bar**; each
  citizen's dossier shows who is driving them. See `docs/SOCIETY_MIND.md`. 77 tests pass.

## Handoff — visual density + background workers (next, for Codex)

See `CODEX_HANDOFF.md` for the full task prompt. Five phases, all truth-derived:
1. **Visual world density** — render real units/citizens as crowds (aggregate far,
   individuals near).
2. **Full zoom continuum** — Planet→Region→City→District→Street→Building→Citizen, no
   abrupt transitions.
3. **AAA mobile visual pass** — roof/road/district textures, bridges, shorelines,
   seasonal vegetation, fog, night lighting (CC0 only, mobile-safe).
4. **City skylines** — wealth/trade/religion/knowledge/war drive height/density/materials/
   monuments so a city is identifiable at a glance.
14. **Background LLM workers** — a worker fleet that proactively narrates new
   biographies/city/religion histories on simulation events, via `engine._narrate` +
   `interp` cache; async, incremental, never blocks the sim.

## Later

### Phase 5 — A true economy *(highest-value next vertical)*
Replace the single `food` value with multi-resource production/transport/consumption.
- Resources: food, wood, stone, metal, energy, luxury goods, knowledge, labor.
- Per-city stockpiles + consumption; production from tiles/buildings/specialties.
- **Emergent trade routes** driven by supply→demand and price; caravans carry specific
  goods; shortages drive migration and war.
- Cities rise and fall on economic reality, not a single food ratio.
- Dashboard: economic map overlay, per-city ledgers, price charts.

### Phase 6 — Emergent technology
No static tech tree. Knowledge as a stock that accumulates and **spreads** through trade,
migration, education, and espionage. Civilizations diverge technologically from their
conditions; tech modifies production, military, and city growth.

### Phase 7 — Scale substrate (toward millions)
Re-architect the hot path onto a data-oriented **ECS** (struct-of-arrays, numpy/torch
batched), **event sourcing** for state, and aggressive **LOD promotion/demotion** so the
sim targets millions of compressed entities with GPU-batched species inference. Foundation
for the planetary-scale target.

### Phase 8 — Deeper politics & diplomacy
Faction–civ policy contests beyond the revolution path: coups, elections, succession,
alliances, vassalage, trade pacts and embargoes. Macro policy assembled from the weighted
incentives of the factions and notable individuals who hold influence.

### Phase 9 — Warfare from real people
Armies composed of named individuals from the pool; casualties remembered by families
(grief memories, blood feuds); sieges, supply lines, and morale. Deaths matter.

### Phase 10 — Distinct cultures
A first-class **Culture** object (traditions, values, taboos, myths, symbols, rituals,
attitudes) that emerges, drifts over centuries, and spreads via trade/migration/conquest/
religion — distinct from, but coupled to, religions and factions.

### Phase 11 — Richer LLM moments
Extend the event-driven LLM beyond the chronicle: diplomatic negotiations, religious
debates, historical speeches, personal journals, letters, and court trials — always
event-driven, never per-tick.

### Phase 12 — Deep observability & history
Follow a **trade route** or a **war** as first-class subjects; generated newspapers and
history books per era; searchable world memory; family trees and dynasty views.

### Phase 13 — Persistence & save/load
Event-sourced, resumable worlds; export/import; long-run stability (centuries of ticks)
without drift or unbounded memory.

### Phase 14 — Graphics & polish
Stylized realism, smooth animation, larger visible populations, day/night and seasons,
LOD-by-camera-distance for units, motion trails, and biome detail.

## Cross-cutting / tech debt

- **Test suite.** Currently smoke-tested by hand (see DEVELOPMENT.md); add pytest for the
  sim invariants (determinism, budgets, clamps) and the directive/interview contracts.
- **Determinism above the core.** LLM + observer focus are intentionally nondeterministic;
  consider a seeded "ghost observer" for reproducible long runs.
- **Config surfacing.** Promote in-code tuning constants (CONFIG.md table) into YAML where
  useful.
- **Local asset vendoring.** Optionally vendor Three.js so the dashboard works offline.
