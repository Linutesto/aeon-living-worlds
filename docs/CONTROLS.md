# Controls

AEON's dashboard is mobile-first (built for a phone in portrait) but works the same on
desktop. Everything is on one screen: the 3D world fills the stage, with control rows
along the top and a tab bar along the bottom.

## Time controls (bottom bar)

| Control | Action |
|---|---|
| ❚❚ | Pause / resume the simulation |
| ¼× … 100× | Set the simulation speed multiplier |

The sim only advances while the server is running and not paused. Higher speeds grow
cities and history faster.

## Map overlays (top row)

Tap a chip to recolor the world by a data layer:

`Territory` · `Nations` (per-civilization tint) · `Trade` · `Economy` · `Population` ·
`Religion` · `Faction` · `Migration` · `War` · `Policy` · `Rebellion` · `Resources` ·
`Wildlife` · `Climate`

## Camera modes (top row)

| Mode | View |
|---|---|
| 🌍 God | Free orbit over the whole world |
| 🏛 Civ | Follow a civilization |
| 🏙 City | Zoom to a city |
| 🚶 Unit | Follow a moving unit (trader, army, migrant) |
| ⏩ Time-lapse | Auto-advancing cinematic camera |

Drag to orbit, scroll/pinch to zoom. Press **Esc** (or *Return to map*) to drop a follow
target.

## Tabs (bottom bar)

| Tab | Panel |
|---|---|
| 🌍 World | World overview, vitals, city/people browsers |
| 🜂 Spirit | The LLM world-spirit: thoughts, goals, directives, LLM scheduler |
| 📜 History | The Chronicle / event timeline (filterable) |
| 🏛 Atlas | Civilizations, religions, factions, cultures to browse and follow |
| 📈 Charts | Population / civilization / economy time-series |
| ⚡ God | Direct interventions and saves |
| ⚙ Setup | New World / Restart, Civilizations, Graphics, Texture Packs, Debug Placement |

## The Setup tab (New World / Graphics)

- **New World / Restart:** edit seed, map size, starting species/population/civilizations,
  and the generation knobs (climate, water, resources, war, tech…), then **Restart with
  these settings**, **same seed**, or **random seed**. A *Keep trained minds* toggle
  carries learned AI across the new world. You can also reset a single layer
  (civilizations / terrain-climate / cities-population / minds).
- **Graphics:** pick a quality **preset** and a **texture pack**, tune render budgets
  (max buildings / particles / lights, LOD distance), and toggle **Debug Placement**
  (tints any building the layout couldn't place cleanly).

See [WORLDGEN.md](WORLDGEN.md) for every editable variable and [TEXTURE_PACKS.md](TEXTURE_PACKS.md)
for the packs.

## Keyboard

| Key | Action |
|---|---|
| `P` | Toggle the **performance HUD** (FPS, draw calls, triangles, LOD, preset, sim tick ms) |
| `Esc` | Stop following / return to the map |

## Inspecting the world

Tap a city, person, religion, or faction to open its panel. Focused cities materialize
their individual citizens — you can read a person's life, family, and biography, and (with
an LLM configured) **interview** them.
