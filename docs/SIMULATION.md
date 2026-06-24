# AEON — The Simulation Core (L0 & L5)

The `sim/` package is the deterministic world. It imports nothing outside `sim/` and
never reaches into the governor, agents, society, or server. Everything is a pure
function of `(seed, params, directives, ticks)`.

## The tick contract

`world.tick(world)` advances one step in a **fixed order** — sequencing lives here,
rules live in the submodules:

```
terrain → climate → resources → species → evolution → civilization
        → cities → units → events → marker decay
```

The engine then runs the **life-tick** (`agents` + `society`) outside the sim core, so
the social layers observe a fully-updated world. Each submodule exposes a
`step(world)` that mutates `world` in place using `world.rng` (named streams) and
`world.params`.

## WorldState

`sim/world.py` holds everything: the grids, the living things, and the id sources.

- **Grids** (`numpy`, `height × width`): `elevation` (−1..1), `water`, `biome` (int ids),
  `temperature` (°C), `humidity`, `rainfall`, `minerals`, `food`, `energy`.
- **Living things**: `species`, `civilizations`, `cities`, `units` (dicts by id).
- **`markers`**: transient world-space events (battles, famines, disasters) with TTL —
  this is how events become *visible* rather than log-only.
- **Helpers**: `land_mask`, `population` (wildlife), `urban_population` (people in cities),
  `add_marker(...)`, and monotonic id allocators.

`BIOME` maps names→ids (`ocean, beach, grassland, forest, desert, mountain, snow, swamp,
tundra`) and is kept in sync with `web/js/world3d.js`.

## WorldParams — the only surface the spirit may write

`sim/params.py` defines `WorldParams` plus a `BOUNDS` registry giving each knob a hard
`[lo, hi]` clamp and a short description (fed verbatim to the LLM). Knobs include
`rainfall_multiplier`, `temperature_bias`, `storm_intensity`, `sea_level`,
`volcanic_activity`, `tectonic_drift`, `resource_richness`, `plant_growth`,
`prey_fertility`, `predator_fertility`, `mutation_rate`, `carrying_capacity`,
`civ_expansion_drive`, `war_propensity`, `tech_progress`. `set()` (absolute) and
`adjust()` (percentage) always clamp, so no directive — however hallucinated — can push
the world invalid.

## L0 — Environment

- **terrain.py** — value-noise heightmap biased toward a central landmass; greedy
  downhill river carving; biome classification from elevation + climate; per-tick slow
  forces (`tectonic_drift`, `volcanic_activity` eruptions that raise new land and drop a
  marker).
- **climate.py** — latitude + elevation baseline temperature relaxed toward a target
  (so biases ease in), humidity advecting off water, rainfall from humidity scaled by
  `rainfall_multiplier`, stochastic storms scaled by `storm_intensity`.
- **resources.py** — food regrows logistically toward a biome ceiling × `plant_growth`;
  minerals/energy seeded once (scaled by `resource_richness`) and deplete.
- **events.py** — the `CATALOG` of cataclysms (`meteor_impact`, `ice_age`, `plague`,
  `resource_boom`, `magical_anomaly`, `volcanic_eruption`, `drought`, `flood`). `apply()`
  is the single entry point for both the spirit and the God Console; effects route
  through params/grids and (for plague/meteor) hit nearby cities + drop markers.

## Ecology — species & evolution

- **species.py** — species are *population-dynamics agents*, not per-individual: a
  genome (heat tolerance, size, speed, aggression, fertility), a population, a centroid
  and spread. Each tick: births/deaths from habitat fit + predation, global crowding
  brake (`world_capacity = land_tiles × TILE_CAPACITY × carrying_capacity`), and gradient
  migration toward better habitat.
- **evolution.py** — thriving species throw off mutant daughters (rate =
  `mutation_rate` × environmental stress × crowding factor); extinctions are recorded.
  `SPECIES_SOFT_CAP` keeps the count legible. Civilizations later emerge from large
  settled herbivore lineages — so the human story is rooted in the ecology.

## L5 — Cities & civilizations

- **cities.py** — a `City` is a real, located place: `population`, `growth_rate`,
  `food_production`, `culture`, `infrastructure` (1–10), `influence_radius`, `wealth`,
  `specialty` (Breadbasket / Mining Town / Trade Port / Cultural Center / Fortress City),
  and `famine`/`plague`/`unrest` state. Cities **emerge** where `site_suitability` is
  high (food + fresh water + temperate + low elevation), **grow or starve** on local
  food harvested from tiles in their influence radius, **expand** their radius with
  population, and **found daughter cities** (respecting `CITY_CAP`). Tiers:
  hamlet < village < town < city < metropolis. Famine raises a visible marker.
- **civilization.py** — a `Civilization` owns `city_ids`, advances `tech`, runs
  `relations` and **diplomacy**: bordering civs erode relations; below a threshold a
  `war_propensity` roll pushes a **war intent** (a from-city/to-city pair) consumed by
  `units.py`. Civs collapse when they lose all cities.

## Units — people you can watch move

`sim/units.py` is what makes the world observable in real time. A `Unit` has a kind,
owner civ, float position, target, speed, and payload:

| kind | code | role |
|------|------|------|
| civilian | 0 | ambient life near a city's edge |
| trader | 1 | short hops between friendly cities, carrying wealth |
| caravan | 2 | long-haul trade between distant cities |
| migrant | 3 | flees famine/unrest toward prosperity |
| explorer | 4 | strikes out from a frontier city |
| army | 5 | raised on a war intent; marches and besieges |

Each tick units spawn by policy (civilians by city size; traders when a city has wealth;
migrants from famine; explorers from prosperous frontiers; armies from civ war intents),
move toward targets, and **resolve on arrival**: traders deliver wealth, migrants join
the destination, armies fight a **battle** that can **conquer** a city (it changes civ
and color). A global `MAX_UNITS` budget keeps it fast; the client interpolates positions
to 60 fps between snapshots.

## Balance & determinism notes

- The deterministic core reproduces exactly for a given seed + directive sequence.
- Tuning constants are documented in [CONFIG.md](CONFIG.md).
- The renderer maps normalized (0..1) entity positions; biome ids must stay in sync with
  `web/js/world3d.js`.
