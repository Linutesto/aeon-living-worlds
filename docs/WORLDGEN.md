# World Generation & Restart

AEON worlds are **deterministic**: the same seed + the same generation config always
produce the same world. You edit these variables in the **Setup** tab, or via the REST
API, then restart.

The single source of truth is `WorldGenConfig` (`aeon/sim/worldgen.py`). It is strictly
validated — unknown keys are rejected, numbers are clamped to their ranges — so a bad
request can never push the simulation into an invalid state. Live schema:
`GET /api/world/config/schema`.

## Editable variables

### Structural

| Key | Default | Range | Meaning |
|---|---|---|---|
| `seed` | 1337 | 0 – 2,147,483,647 | World RNG seed (determinism) |
| `width` | 192 | 64 – 384 | Map width in tiles |
| `height` | 192 | 64 – 384 | Map height in tiles |
| `name` | "Aeon-Prime" | — | World name |
| `start_species` | 6 | 1 – 20 | Wildlife lineages seeded at genesis |
| `start_population` | 4000 | 100 – 40,000 | Total wildlife population at genesis |
| `start_civilizations` | 5 | 1 – 12 | Distinct rival nations seeded at genesis |

### Generation knobs (`params`)

These are the same clamped knobs the world-spirit may nudge, so a player-set value has the
exact same deterministic effect.

| Key | Default | Range | Meaning |
|---|---|---|---|
| `rainfall_multiplier` | 1.0 | 0.1 – 4.0 | Global rainfall scale |
| `temperature_bias` | 0.0 | −25 – 25 | Degrees °C added everywhere |
| `storm_intensity` | 1.0 | 0.0 – 4.0 | Frequency/severity of storms |
| `sea_level` | 0.0 | −0.3 – 0.3 | Ocean height offset (drowns/exposes land) |
| `volcanic_activity` | 0.0 | 0.0 – 1.0 | Chance of eruptions reshaping terrain |
| `tectonic_drift` | 0.0 | 0.0 – 1.0 | Rate of slow elevation change |
| `resource_richness` | 1.0 | 0.2 – 3.0 | Mineral/energy abundance |
| `plant_growth` | 1.0 | 0.2 – 3.0 | Vegetation/food regrowth rate |
| `prey_fertility` | 1.0 | 0.2 – 3.0 | Herbivore birth rate |
| `predator_fertility` | 1.0 | 0.2 – 3.0 | Predator birth rate |
| `mutation_rate` | 0.02 | 0.0 – 0.5 | Per-generation mutation chance |
| `carrying_capacity` | 1.0 | 0.3 – 3.0 | How much life the land supports |
| `civ_expansion_drive` | 1.0 | 0.0 – 3.0 | How aggressively civs settle new land |
| `war_propensity` | 1.0 | 0.0 – 3.0 | Likelihood civs go to war |
| `tech_progress` | 1.0 | 0.0 – 3.0 | Rate of civilization tech advance |

### Presentation (cosmetic — never affects determinism)

| Key | Default | Range / options | Meaning |
|---|---|---|---|
| `graphics_preset` | `desktop` | emergency, mobile-low, mobile-high, desktop, ultra, rtx-4090-ultra | Render quality preset |
| `texture_pack` | `default-clean` | see [TEXTURE_PACKS.md](TEXTURE_PACKS.md) | Active texture pack |
| `lod_distance` | 1.0 | 0.2 – 4.0 | LOD distance multiplier |
| `max_buildings` | 18000 | 500 – 40,000 | Max buildings rendered |
| `max_particles` | 6000 | 0 – 20,000 | Max particles rendered |
| `max_lights` | 800 | 0 – 4,000 | Max dynamic lights rendered |
| `city_density` | 1.0 | 0.2 – 2.0 | Relative city count |
| `building_density` | 1.0 | 0.2 – 2.0 | Relative buildings per city |
| `road_density` | 1.0 | 0.2 – 2.0 | Relative road coverage |

## Restart & layer resets

`restart` rebuilds the world in-process from the current/edited config. Minds (the learned
AI) reset fresh by default; a **Keep trained minds** toggle carries them across the new
world. You can also reset a single layer:

| Layer | Effect |
|---|---|
| `civilization` | Keep terrain/climate/species; rebuild the whole political + social stack |
| `terrain_climate` | Regenerate terrain (a full rebuild, since everything sits on it); keeps minds |
| `cities_population` | Keep terrain + civ identities; wipe cities and people, re-found capitals |
| `minds` | Reset only the AI/learning state |

## REST API

| Method & path | Body | Purpose |
|---|---|---|
| `GET /api/world/config/schema` | — | Field types/ranges/defaults (drives the UI) |
| `GET /api/world/config` | — | Current generation config |
| `POST /api/world/restart` | `{config, keep_minds}` | Restart with a (partial) config |
| `POST /api/world/restart/random` | `{config?, keep_minds}` | Restart with a fresh random seed |
| `POST /api/world/reset-layer` | `{layer}` | Reset one layer |
| `GET/POST /api/graphics/preset(s)` | `{preset, …budgets}` | List / set the quality preset |
| `GET/POST /api/texture-pack(s)` | `{pack}` | List / set the active texture pack |

A partial config is merged onto the current one and re-validated. Example bodies are in
[`examples/configs/`](../examples/configs/) — e.g.:

```bash
curl -X POST localhost:8080/api/world/restart \
  -H 'content-type: application/json' \
  --data @examples/configs/harsh-ice-age.json
```

(The example files are the `config` object; wrap as `{"config": {…}}` if your client
needs the control fields too.)

## Persistence

Saves are versioned and carry the full generation config, graphics preset, texture pack,
render budgets, and restart lineage, so loading a save restores the exact world setup.
Older saves load with defaults for any missing fields.
