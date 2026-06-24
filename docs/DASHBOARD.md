# AEON — The Dashboard

A mobile-first web dashboard (vanilla JS + Three.js, **no build step**) designed for the
Pixel 9 Pro XL in portrait and scaling up to desktop. The renderer is a first-class
part of the experience, not an admin panel. Served from `web/` at `http://<host>:8080`.

## Structure

```
web/
  index.html        shell: status bar, 3D stage, overlay/camera chips, panel, tab bar
  css/styles.css    dark, mobile-first, large touch targets, single wide breakpoint
  js/
    ws.js           the reactive store + WebSocket client + REST helpers (api/post)
    main.js         bootstrap: status bar, tabs→panels, overlays, camera modes, toasts
    world3d.js      the Three.js renderer (terrain, cities, units, territory, events)
    timecontrols.js pause / 1×…100× speed
    toast.js        transient event banner
    dashboard.js    World panel (vital signs + world memory)
    governor.js     Spirit panel (LLM mind + Level-2 species-AI status)
    timeline.js     History panel (Timeline ⇄ Chronicle)
    inspectors.js   Atlas panel (People/Cities/Civs/Religions/Factions/Wildlife + interview)
    metrics.js      Charts panel (canvas sparklines)
    godconsole.js   God panel (intervention buttons)
```

`ws.js` exposes a tiny reactive store: `store.on(type, fn)` subscribes to a payload type
(replaying the last value), `store.state[type]` is the latest. One WebSocket carries
every payload type; control messages go back up the same socket.

## The 3D world (`world3d.js`)

- **Terrain:** heightmap mesh colored by biome with relief shading, plus a translucent
  animated **sea plane** so oceans read as water.
- **Cities:** instanced building clusters whose count/height scale with population and
  infrastructure; a civ-colored **influence ring**; a glow for great cities; **name
  labels** for towns and up; an invisible pick sphere for tap-to-inspect.
- **Territory & trade:** soft civ-colored discs (borders you can see) and a
  nearest-neighbor **trade-route road network**.
- **Units:** one `InstancedMesh` of up to `UNIT_MAX` movers, colored by kind, **position-
  interpolated to 60 fps** between `live` snapshots so the world is never static.
- **Events:** animated beacons for battles, meteors, eruptions, migrations, revolutions,
  founded faiths (famine/plague show as a city tint instead).
- **Camera modes** (tweened): **God** (free orbit), **Civilization** (frame a civ's
  cities), **City** (zoom in), **Unit** (follow a mover), **Time-lapse** (auto-rotate +
  request high speed). Tapping a city emits `city-pick` → opens its inspector.

## Tabs (bottom bar)

| tab | panel | what you do |
|-----|-------|-------------|
| 🌍 World | `dashboard.js` | vital signs + the world's myths (world memory) |
| 🜂 Spirit | `governor.js` | the LLM governor's philosophy, goal, decisions, active params, and the **Level-2 species-AI** status (backend, updates, loss, pool size) |
| 📜 History | `timeline.js` | **Timeline** (filterable events) ⇄ **Chronicle** (the LLM history book) |
| 🏛 Atlas | `inspectors.js` | browse **People / Cities / Civilizations / Religions / Factions / Wildlife**; open any dossier; **interview** a person; **Focus camera** on anything |
| 📈 Charts | `metrics.js` | sparklines: population, cities, biodiversity, civilizations, temperature, health |
| ⚡ God | `godconsole.js` | direct interventions (meteor, ice age, plague, boosts, spawns) |

## Map overlays

`Territory` (civ-colored regions + roads), `Trade` (routes), `Wildlife` (species),
`Climate`. A legend explains the moving unit colors (trade / migrants / army / explorer).

## The "follow anything" experience

Every layer is explorable and cross-linked:

- Tap a **city** in 3D → its dossier → **View residents** → a **person** → **interview**
  them, or jump to their **kin/rivals**, their **faith**, or their **factions**.
- Open a **religion** → its founder, tenets, lands of the faith → **Focus camera** on its
  holy city.
- Open a **faction** → its members (tap to inspect) → its seat city.
- History → **Chronicle** to read the world remembering itself.

## Person dossier & interview

The Atlas → People flow shows a resident's profile, Big-Five **personality bars**,
**Faith & Allegiance** (religion, factions, grievance), drives/skills, relationships
(tap to jump), life history, and remembered events. The **Interview** box has preset
questions ("Who are you?", "Why did you leave your city?", "Who are your enemies?") and
a free-text field; answers come from the local LLM grounded in the person's real state.
