# AEON — Asset Manifest

AEON renders a **procedural foundation** (geometry generated in code) enhanced with
**real CC0 textures**. All bundled textures are **CC0 1.0 / Public Domain** from
**Poly Haven** — no attribution required, free to redistribute. Files live in
`web/assets/textures/` as 512×512 JPGs for mobile profiles, with matching 2048×2048
desktop/ultra copies in `web/assets/textures/2k/`.

A full license table is also maintained in [ASSET_LICENSES.md](ASSET_LICENSES.md).
The current art-direction plan is in [VISUAL_ART_DIRECTION.md](VISUAL_ART_DIRECTION.md).

| File | Source (Poly Haven) | License | Usage in AEON |
|------|---------------------|---------|---------------|
| `grass.jpg` | aerial_grass_rock | CC0 1.0 | Terrain splat — grassland biome |
| `dirt.jpg` | brown_mud_leaves_01 | CC0 1.0 | Terrain splat — earth/swamp |
| `mud.jpg` | mud_cracked_dry_03 | CC0 1.0 | Terrain splat — dried mud |
| `rock.jpg` | rocks_ground_06 | CC0 1.0 | Terrain splat — mountains/highlands |
| `rock2.jpg` | rocky_terrain_02 | CC0 1.0 | Terrain splat — cliffs |
| `sand.jpg` | aerial_beach_01 | CC0 1.0 | Terrain splat — beach/desert |
| `snow.jpg` | snow_02 | CC0 1.0 | Terrain splat — snow/tundra/peaks |
| `forest.jpg` | forrest_ground_01 | CC0 1.0 | Terrain splat — forest floor |
| `wood.jpg` | plank_flooring_02 | CC0 1.0 | Building walls — wood/timber/thatch |
| `stone.jpg` | medieval_blocks_02 | CC0 1.0 | Building walls — stone (temples, barracks) |
| `brick.jpg` | red_brick_03 | CC0 1.0 | Building walls — brick (high-infrastructure) |
| `plaster.jpg` | painted_plaster_wall | CC0 1.0 | Building walls — plaster (default) |
| `rooftile.jpg` | roof_tiles_14 | CC0 1.0 | Reserved for tiled roofs |

## Expanded visual-believability texture library

All files below are also 512×512 JPGs from Poly Haven CC0 1.0 diffuse maps.

| File | Source (Poly Haven) | License | Usage in AEON |
|------|---------------------|---------|---------------|
| `dry_grass.jpg` | withered_grass | CC0 1.0 | Dry grass, arid seasonal tint |
| `mud_wet.jpg` | brown_mud_03 | CC0 1.0 | Wet mud, marshes, river banks |
| `gravel.jpg` | gravel_ground_01 | CC0 1.0 | Gravel terrain, mine ground |
| `beach.jpg` | coast_sand_01 | CC0 1.0 | Beaches and coast transitions |
| `cliff.jpg` | cliff_side | CC0 1.0 | Slope-aware cliffs |
| `ice.jpg` | snow_01 | CC0 1.0 | Ice and hard winter snow |
| `marsh.jpg` | brown_mud_rocks_01 | CC0 1.0 | Marsh/swamp ground |
| `farmland.jpg` | dry_mud_field_001 | CC0 1.0 | Farm plots near real farm buildings |
| `forest_floor.jpg` | forrest_ground_03 | CC0 1.0 | Forest floor under tree instances |
| `moss.jpg` | mossy_rock | CC0 1.0 | Moss/lichen detail, wet highlands |
| `riverbed.jpg` | mud_cracked_dry_riverbed_002 | CC0 1.0 | Riverbed strips under real rivers |
| `dirt_road.jpg` | aerial_mud_1 | CC0 1.0 | Dirt road material |
| `packed_earth.jpg` | grass_path_2 | CC0 1.0 | Paths / low traffic roads |
| `gravel_road.jpg` | gravel_ground_01 | CC0 1.0 | Medium traffic roads |
| `cobblestone.jpg` | cobblestone_floor_001 | CC0 1.0 | Prosperous district paving |
| `stone_road.jpg` | medieval_blocks_05 | CC0 1.0 | Major/imperial road surfaces |
| `bridge_wood.jpg` | brown_planks_09 | CC0 1.0 | Wooden bridges, harbor decking |
| `bridge_stone.jpg` | monastery_stone_floor | CC0 1.0 | Stone bridges / temple paving |
| `clay_wall.jpg` | clay_block_wall | CC0 1.0 | Clay/adobe walls and poor housing |
| `fortress_stone.jpg` | defense_wall | CC0 1.0 | Fortresses, barracks, walls |
| `palace_stone.jpg` | granite_wall | CC0 1.0 | Palace/manor stone |
| `ruined_masonry.jpg` | rabdentse_ruins_wall | CC0 1.0 | Ruins, damaged masonry |
| `thatch.jpg` | reed_roof_03 | CC0 1.0 | Thatch roofs / slums |
| `wood_shingle.jpg` | brown_planks_04 | CC0 1.0 | Wood shingle roofs and timber walls |
| `clay_tile.jpg` | clay_roof_tiles_03 | CC0 1.0 | Clay tile roofs |
| `slate.jpg` | roof_slates_02 | CC0 1.0 | Slate roofs, fortification roofs |
| `temple_roof.jpg` | ceramic_roof_01 | CC0 1.0 | Temple/shrine roofs |
| `metal_roof.jpg` | corrugated_iron | CC0 1.0 | Academy/observatory metal roofs |
| `rubble.jpg` | brick_gravel | CC0 1.0 | Rubble decals |
| `ash.jpg` | burned_ground_01 | CC0 1.0 | Ash/burn marks |
| `burned_ground.jpg` | burned_ground_01 | CC0 1.0 | Burned terrain decals |
| `market_plaza.jpg` | brick_pavement_02 | CC0 1.0 | Market plazas |
| `harbor_wood.jpg` | brown_planks_09 | CC0 1.0 | Docks and waterfront districts |
| `temple_stone.jpg` | monastery_stone_floor | CC0 1.0 | Sacred district paving |
| `academy_stone.jpg` | marble_tiles | CC0 1.0 | Academy/scholarly district paving |

**Mobile source root:** `https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/<asset>/<asset>_diff_1k.jpg`
**Desktop/ultra source root:** `https://dl.polyhaven.org/file/ph-assets/Textures/jpg/2k/<asset>/<asset>_diff_2k.jpg`

Every texture listed above has two local delivery tiers:

- `web/assets/textures/<file>` — 512×512 mobile-safe JPG.
- `web/assets/textures/2k/<file>` — 2048×2048 desktop/ultra JPG from the same Poly
  Haven asset and CC0 license.

## How textures map to simulation truth (core rule)

Textures only *reveal* state, never invent it:

- **Terrain** uses triplanar splat mapping with a 12-layer biome material resolver;
  per-vertex blend weights come from real
  `biome`, `elevation`, `water`, `rainfall`, `temperature`, `fertility`, and local
  render-derived `smoothed_height`, `slope`, `cliff_mask`, `beach_mask`, `snow_mask`,
  `riverbank_mask`, `wetland_mask`, `farmland_visual_zone`, `moss_mask`,
  `volcanic_mask`, and `settlement_visual_zone`. Steep slopes pull cliff/rock, cold
  high regions pull snow/ice, wet lowlands pull mud/marsh, farm zones pull farmland,
  volcano/impact markers pull ash/burned ground, beaches pull coast sand, and fertile
  forest cells pull forest floor.
- **Buildings** are textured by `material` (`projection._building_material`), which the
  simulation derives from culture / prosperity / infrastructure, with archetype-specific
  overrides for temples, palaces, academies, fortresses, docks, and ruins. Instance
  colour still modulates by wealth / condition / damage.
- **Roads, bridges, riverbeds, plazas, and farms** are render-only surfaces placed only
  where the projection already reports real roads, bridges, rivers, districts, and farm
  buildings.

## Runtime library

- **Three.js r160** — MIT — loaded from unpkg via the import map in `web/index.html`
  (`three`, `three/addons/` including `BufferGeometryUtils`).

## Adding assets later

Restrict to CC0 / Public Domain / MIT / BSD. Preferred: Poly Haven, ambientCG, Kenney,
OpenGameArt (permissive entries only). Vendor under `web/assets/`, record here and in
`ASSET_LICENSES.md` with source URL + license. Reject anything with unclear licensing.
