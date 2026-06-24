# AEON — Server API Reference

The FastAPI app (`aeon/server/app.py`) serves the static dashboard, a REST API, and a
single WebSocket. All entity positions in payloads are **normalized to 0..1**
(`x = col/width`, `y = row/height`) so clients are resolution-independent. The engine is
the only serialize surface; the server holds no simulation logic.

## WebSocket — `GET /ws`

The live channel. On connect the server sends a **full snapshot** (one message per type:
`terrain, overview, cities, live, wildlife, governor, memory, metrics, society`), then
the broadcaster pushes incremental updates at tiered cadences:

| payload | rate (at `broadcast_hz`=12) | contents |
|---------|------|----------|
| `live` | every cycle (~12 Hz) | `{t, units:[{id,k,c,x,y}], markers:[{kind,x,y,age,ttl,label}]}` — `k`=kind code, `c`=civ id |
| `overview` | every cycle | `{stats:{…}, speed, paused}` — the vital-signs snapshot |
| `cities` | every 3 (~4 Hz) | `{cities:[…], civs:[…], routes:[[x1,y1,x2,y2,civ]]}` |
| `governor` | every 6 (~2 Hz) | spirit thought/goal/directives + `species_ai`, `pool`, `society` counts, `params` |
| `metrics` | every 12 (~1 Hz) | `{series:{name:[[tick,value],…]}}` |
| `memory` | every 12 | governor myths + philosophy + goal history |
| `wildlife` | every 12 | species clouds for the Life overlay |
| `society` | every 12 | `{religions:[…], factions:[…]}` |
| `terrain` | every `terrain_every` (120) | heightmap + biome grid (heavy) |

**Client → server control messages** (JSON over the same socket), handled by
`_handle_client_msg`:
```json
{"action":"speed","speed":5}      // 0 = pause
{"action":"pause"}
{"action":"god","op":"trigger_event","payload":{"kind":"meteor_impact"}}
```

The client interpolates `live` unit positions between snapshots for 60 fps motion.

## REST

### World & history
| method | path | returns |
|--------|------|---------|
| `GET` | `/api/state` | overview + governor + memory (one-shot of the live snapshot) |
| `GET` | `/api/timeline?type=&limit=` | filtered event timeline (`type` optional) |
| `GET` | `/api/metrics` | all charted time-series |
| `GET` | `/api/chronicle` | the LLM-written history book (recent entries) |
| `POST` | `/api/speed` | `{speed: 0..100}` → `{speed}` |
| `GET` | `/api/saves` | list SQLite save slots and autosave metadata |
| `POST` | `/api/save` | `{slot}` → saves the full world state to that slot |
| `POST` | `/api/load` | `{slot}` → loads the full world state from that slot |

### Inspectors
| method | path | returns |
|--------|------|---------|
| `GET` | `/api/species/{id}` | a wildlife species dossier |
| `GET` | `/api/civ/{id}` | a civilization (cities, tech, relations, history) |
| `GET` | `/api/city/{id}` | a city (pop, growth, food, culture, infra, influence, state) |
| `GET` | `/api/city/{id}/people` | **focuses** the city (materializes residents) and returns the roster |
| `GET` | `/api/people?city_id=&q=&limit=&focus=` | searchable directory of materialized citizens; `focus=true` promotes one city's residents first |
| `GET` | `/api/person/{id}` | a full person dossier (profile, Big Five, ideology, kin, faith/factions, memories, milestones) |
| `POST` | `/api/person/{id}/ask` | `{question}` → `{name, question, answer}` — grounded in-character interview |
| `GET` | `/api/religion/{id}` | a faith (founder, tenets, holy city, cities-of-faith, schism lineage) |
| `GET` | `/api/faction/{id}` | a faction (kind, goal, founder, seat, influence, members) |

### God Console (`server/god_console.py`)
| method | path | returns |
|--------|------|---------|
| `GET` | `/api/god/presets` | the friendly intervention buttons |
| `POST` | `/api/god/action` | `{op, key?, value?, kind?, diet?, …}` → `{ok, message}` |

God actions funnel through the **same validated `Directive` path** as the world-spirit
(`governor/directives.py`): whitelisted ops, clamped values. Ops: `set_param`,
`adjust_param`, `trigger_event`, `spawn_species`, `set_goal`, `add_myth`.

## `overview.stats` keys

`world_age, population` (people in cities), `wildlife, species_count, civilization_count,
city_count, unit_count, largest_city, dominant_civ, famine_count, biodiversity (+label),
climate_stability (+label), avg_temperature, war_frequency, world_health,
dominant_species, active_events, params`.

## Notes

- 404s return `{"error":"not found"}`; the dashboard renders these as "gone to history".
- `society` is delivered over the WebSocket, not REST (there is no `/api/society`); the
  dashboard reads it from the live store.
- Focusing a city via `/api/city/{id}/people` is what drives the LOD materialization
  (see [INDIVIDUALS.md](INDIVIDUALS.md)).
