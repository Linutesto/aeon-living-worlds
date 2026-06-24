# AEON — Configuration Reference

All runtime configuration lives in `config.yaml` at the project root, parsed into typed
dataclasses by `aeon/config.py`. Override the file path with the `AEON_CONFIG`
environment variable. Unknown keys are ignored; missing keys fall back to the defaults
shown below.

## `world`
| key | default | meaning |
|-----|---------|---------|
| `seed` | `1337` | master RNG seed. Same seed + same directives ⇒ same deterministic core. |
| `width` | `192` | grid columns |
| `height` | `192` | grid rows |
| `name` | `"Aeon-Prime"` | world name (shown at genesis) |

## `sim`
| key | default | meaning |
|-----|---------|---------|
| `tick_seconds` | `0.2` | wall-clock seconds between sim ticks (before the speed multiplier) |
| `max_speed` | `100` | cap for the dashboard time-control multiplier |
| `start_species` | `6` | seed species at genesis |
| `start_population` | `4000` | total seed wildlife population |

## `governor`
| key | default | meaning |
|-----|---------|---------|
| `enabled` | `true` | run the world-spirit loop |
| `backend` | `ollama` | LLM backend (only Ollama implemented) |
| `model` | `jaahas/qwen3.5-uncensored:2b` | any Ollama model id; small = low latency |
| `base_url` | `http://localhost:11434` | Ollama server |
| `tick_seconds` | `20` | seconds between deliberations (the spirit runs slow, off the sim's hot path) |
| `temperature` | `0.9` | sampling temperature (creative on purpose) |
| `max_tokens` | `800` | `num_predict` for the model |
| `timeout_seconds` | `45` | per-request timeout |
| `think` | `false` | disable hidden chain-of-thought (see DEVELOPMENT.md gotcha) |
| `event_base_chance` | `0.05` | base per-tick odds for spirit-triggered god events |

The governor `LLMClient` is shared by the **chronicle** and **interview** systems too.

## `server`
| key | default | meaning |
|-----|---------|---------|
| `host` | `0.0.0.0` | bind address |
| `port` | `8080` | HTTP/WebSocket port |
| `broadcast_hz` | `12` | live (units/markers) pushes per second — drives smooth motion |
| `terrain_every` | `120` | send the heavy terrain grid only every N broadcast cycles |

Broadcast cadence (cycle = 1/`broadcast_hz`s): `live`+`overview` every cycle; `cities`
every 3; `governor` every 6; `metrics`+`memory`+`wildlife`+`society` every 12; `terrain`
every `terrain_every`. See [API.md](API.md).

## `telemetry`
| key | default | meaning |
|-----|---------|---------|
| `history_max_events` | `5000` | ring buffer size for the event timeline |
| `metrics_window` | `2000` | samples retained per charted time-series |

## Tuning constants (in code, not YAML)

Some balance knobs live as module constants so they stay close to the logic:

| constant | file | default | effect |
|----------|------|---------|--------|
| `TILE_CAPACITY` | `sim/species.py` | `1500` | wildlife carrying capacity per land tile |
| `SPECIES_SOFT_CAP` | `sim/evolution.py` | `80` | soft cap on living species (suppresses speciation above it) |
| `FOOD_PER_CAPITA` | `sim/cities.py` | `0.0013` | food a citizen needs per tick (sets city sizes) |
| `CITY_CAP` | `sim/cities.py` | `60` | global cap on living cities (keeps the map legible) |
| `MAX_UNITS` / `MAX_CIVILIANS` | `sim/units.py` | `340` / `150` | visible moving-unit budget |
| `MAX_PEOPLE` | `agents/population.py` | `4000` | persona-pool budget |
| `LIFE_INTERVAL` | `agents/population.py` | `12` | sim ticks between individual life updates |
| `TARGET_BY_TIER` | `agents/population.py` | 8–80 | residents materialized per focused city by tier |
| schism cooldown / cap | `society/religion.py` | `200` / `40` | min ticks between a faith's schisms; soft cap on living faiths |
| founding odds | `society/religion.py`, `faction.py` | `0.06` / `0.09` | per-society-step chance to found a faith / faction |
