# AEON — Architecture

AEON is an AI-governed synthetic-civilization simulator. A fast, deterministic
simulation core runs the world; a local LLM acts as a "world spirit" that bends the
*rules* (never the outcomes); a bounded pool of fully-realized individuals lives
inside the world's cities; emergent religions and factions turn individual beliefs
into macro politics; and a mobile-first web dashboard lets you watch and steer it all.

```
                         ┌───────────────────────────────────────────┐
                         │                 ENGINE                      │
                         │  owns the world + runs 4 async loops:       │
                         │   sim · governor · mind(species) · chronicler│
                         └───────────────┬─────────────────────────────┘
            ┌───────────────────┬────────┼─────────────┬───────────────────┐
            ▼                   ▼        ▼             ▼                   ▼
        sim/ (L0/L5)      agents/ (L1)  ai/ (L2)   society/ (L3/L4)   governor/ (L4 spirit)
   terrain climate        persona pool  per-species  beliefs           Ollama LLM
   resources species      memory        neural       religions         directives (clamped)
   evolution cities       interview     policy        factions          myths + goals
   units events           (LOD)         (GPU)         chronicle (LLM)
            │                   │            │             │                   │
            └─────────── telemetry/ (stats · history · metrics) ──────────────┘
                                         │
                                  server/ (FastAPI + WebSocket)
                                         │
                                  web/ (Three.js + mobile dashboard)
```

## The cognitive hierarchy

The protocol calls for layered intelligence; AEON maps it onto packages:

| Level | Concern | Where | Mechanism |
|------|---------|-------|-----------|
| **L0** | Environment | `sim/terrain,climate,resources,events` | deterministic numpy grids |
| **L1** | Individuals | `agents/` | utility + traits (cheap), LOD persona pool |
| **L2** | Species minds | `ai/species_policy.py` | per-species neural policy (PyTorch/GPU, numpy fallback), Advantage-Weighted Regression with an entropy floor + KL trust region |
| **L3** | Faiths / cultures | `society/religion.py`, `beliefs.py` | emergent religions, ideology |
| **L4** | Factions / spirit | `society/faction.py`, `governor/` | guilds/orders/revolutions; the world-spirit LLM |
| **L5** | Civilizations | `sim/civilization.py`, `cities.py` | cities, diplomacy, war, territory |

> **The one invariant that makes it all work:** the simulation is the only thing that
> mutates the world. The governor LLM may *only* emit clamped `Directive`s
> (`governor/directives.py`); the society and agent layers may only nudge state through
> their own `step()` functions. No layer paints outcomes by hand.

## Packages

```
aeon/
  config.py        typed config loaded from config.yaml (env AEON_CONFIG overrides)
  rng.py           deterministic, named RNG streams (blake2b-derived sub-seeds)
  engine.py        orchestrator: owns WorldState + all subsystems; the only serialize surface

  sim/             the deterministic world (imports nothing outside sim/)
    world.py       WorldState container + master tick() (fixed order); BIOME ids
    params.py      WorldParams — the clamped knobs the governor may touch (BOUNDS)
    terrain.py     elevation, oceans, rivers, biomes, tectonics, volcanism
    climate.py     temperature, humidity, rainfall, storms
    resources.py   food (renewable), minerals, energy
    species.py     creatures/plants/predators as population-dynamics agents
    evolution.py   mutation, speciation, extinction (SPECIES_SOFT_CAP)
    cities.py      City — pop, growth, food, culture, infrastructure, influence (CITY_CAP)
    civilization.py Civilization — owns cities, diplomacy, war intents
    units.py       moving people: traders/caravans/migrants/explorers/armies/civilians
    events.py      god-mode + natural cataclysms (CATALOG) + world markers

  agents/          L1 — the individual layer (LOD persona pool)
    person.py      Person record (profile, Big Five, goals, skills, ideology, kin…)
    memory.py      decaying episodic memory (salient/emotional memories survive)
    traits.py      personality/skill/name generators + utility cognition (ACTIONS)
    population.py  PopulationManager: materialize-on-focus, life-event tick, demote
    interview.py   build a grounded dossier; the LLM answers in-character

  ai/              L2 — per-species learning
    species_policy.py  SpeciesBrain: one policy per species, AWR (torch|numpy), drift-guarded

  mind/            the "society mind" — a teacher→student distillation stack (prototype)
    teacher.py     batches citizen cohorts to a larger LLM for labels
    trainer.py     trains a live liquid (CfC) student net on those labels
    runtime.py     HybridMind: routes how much of the population the student drives
    dataset.py     the corpus + channels; ingest_traces.py warm-starts it (optional)

  sim/worldgen.py  WorldGenConfig — validated, editable generation config (restart system)

  render/          server-side render projection (sim state → renderer payloads)
    projection.py  builds terrain/city/building/overlay payloads per chunk
    placement.py   deterministic, collision-free city building layout

  society/         L3/L4 — emergent macro structures
    beliefs.py     ideology axes + grievance (the macro→micro coupling)
    religion.py    faiths: found, spread, convert, schism, holy war
    faction.py     guilds/leagues/orders/revolutions; influence → revolution → new civ
    chronicle.py   event-driven LLM history book
    __init__.py    Society: ties them together; step(world, population)

  governor/        L4 — the world spirit (LLM)
    llm.py         Ollama client (think:false; format_json toggle; offline fallback)
    governor.py    deliberate(): stats → prompt → parse → apply directives
    directives.py  Directive schema + whitelist + clamp + safe apply
    prompts.py     system + tick prompt; tolerant JSON parse
    memory.py      GovernorMemory: philosophy, goal, myths, decisions (persisted)

  telemetry/       read-only observation (never mutates the world)
    stats.py       the world-statistics snapshot (governor + dashboard read this)
    history.py     append-only event timeline
    metrics.py     rolling time-series for charts

  server/          transport (no sim logic)
    app.py         FastAPI app, REST routes, /ws, static dashboard, lifespan
    schemas.py     pydantic request/response models
    god_console.py player intervention presets → same directive path
    broadcaster.py pushes serialized state to clients at tiered cadences

web/               mobile-first dashboard (vanilla JS + Three.js, no build step)
```

## Data flow

1. **Engine** constructs `WorldState` (genesis), a `PopulationManager`, a `SpeciesBrain`,
   a `Governor`, and a `Society`.
2. **`_sim_loop`** advances `world_mod.tick(world)` (the L0/L5 sim) at `sim.tick_seconds`
   × the dashboard speed multiplier. After each batch it runs the **life-tick**:
   `population.tick(world)` (L1 lives) and, when a life-tick actually ran,
   `society.step(world, population)` (L3/L4 emergence). Notable events flow to `history`.
3. **`_governor_loop`** wakes every `governor.tick_seconds`, asks the LLM to deliberate
   on a `stats` snapshot, and applies clamped directives (L4 spirit).
4. **`_mind_loop`** periodically trains the per-species policies on buffered experience.
5. **`_chronicler_loop`** drains `society.pending_chronicle` (major events) and asks the
   LLM to write history-book passages.
6. **Broadcaster** serializes world state into JSON payloads and pushes them to the
   dashboard over the WebSocket at tiered rates (see [API.md](API.md)).

## Level-of-detail (how "millions" is honored)

Holding millions of rich agents is infeasible on one machine, so AEON concentrates
fidelity where the observer is looking:

- A city's statistical `population` (a float) always exists for every city.
- When a city is **focused** (selected/zoomed, or promoted by the society layer to find
  a founder), its residents are **materialized** into full `Person` objects with
  families, rivals, and history seeded from the city's real record.
- The pool is budget-capped (`MAX_PEOPLE`); when over budget, the least-notable
  individuals in unfocused cities are released. Re-focusing re-materializes them.

So every person you can inspect or interview is real and persistent for as long as
they matter, while the world as a whole stays light.

## Determinism

The sim core is a pure function of `(seed, params, directives, ticks)` and uses named
RNG streams (`rng.py`) so subsystems don't perturb each other. Full reproducibility
holds for the deterministic core. Two sources of nondeterminism are intentional and
sit *above* the core: the **LLM** (governor decisions, chronicle prose, interviews)
and **observer focus** (which cities get materialized, when). See
[SIMULATION.md](SIMULATION.md) for the tick contract.

## Restart, config & texture packs

- **`sim/worldgen.py:WorldGenConfig`** is the single, strictly-validated bundle of editable
  generation variables (structural ints + the clamped `params.BOUNDS` knobs + cosmetic
  presentation). `create_world(cfg, params=…)` injects params *before* genesis seeding, so
  the same seed + config always reproduce the same world.
- **`engine.restart()` / `reset_layer()`** rebuild the world in-process (minds reset fresh
  by default, opt-in keep). Layers: civilization / terrain-climate / cities-population /
  minds. Exposed at `/api/world/restart[/random]`, `/api/world/reset-layer`.
- **Texture packs** (`web/assets/texturepacks/`) are CC0 remaps selected via
  `/api/texture-pack`; the chosen preset + pack ride the `overview` snapshot. See
  [TEXTURE_PACKS.md](TEXTURE_PACKS.md) and [WORLDGEN.md](WORLDGEN.md).

## Persistence

- **World saves** — `persistence.py:SaveStore` keeps versioned snapshots in
  `saves/aeon_saves.sqlite` (world state + the full generation config, graphics preset,
  texture pack, render budgets, and restart lineage). Trained model weights live alongside
  in `saves/policy_weights/`. Old saves load with defaults for any missing fields.
- **Governor state** — `world_memory.json` (philosophy, goals, myths) and
  `world_chronicle.json` (the LLM-written history book) are saved on clean shutdown and
  loaded at boot. These runtime dumps are git-ignored.

> Local saves, weights, and runtime dumps are **not** committed to the repo (see
> `.gitignore`); they are per-machine state.
