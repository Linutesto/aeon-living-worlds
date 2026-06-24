# AEON Visual Art Direction

AEON targets a stylized civilization-map look: readable from mobile, rich on desktop,
and grounded entirely in simulation state. The renderer should feel like a living
historical world, not a terrain-debug view.

## Implemented Pass

- Terrain uses a 12-layer biome material resolver:
  grass, dry grass, mud, rock, cliff, beach, snow, forest floor, marsh, farmland,
  moss, and ash/volcanic ground.
- Render-only geography masks are projected from real simulation state:
  smoothed height, slope, cliff, beach, snow, riverbank, wetland, farm zone, moss,
  volcanic/impact scars, road suitability, and settlement influence.
- Desktop and ultra profiles load the 2048px texture tier. Mobile profiles keep the
  512px texture tier.
- The persistent fallback remains low-detail for visual coverage; high-detail chunks
  stream over it.
- Settings expose live graphics, visual, controls, world, and debug controls.

## Biome System

Biome identity is built from real terrain facts:

- Grasslands: warm greens, fertility tint, low slope, visible settlement/farm blending.
- Forests: darker green ground, forest-floor texture, moss in wet elevated regions.
- Mountains: slope-aware rock/cliff materials, high/cold snow caps, lighter steep-face
  shading for map readability.
- Deserts: dry grass/sand/beach material mix, warmer low-rainfall palette.
- Tundra: pale dry rock, snow, moss, and cold-season tinting.
- Wetlands: marsh and mud materials from biome 7, water, and fertility.
- Volcanic/impact regions: ash/burned ground around real volcano/meteor markers.

## Rendering Plan

Near and far views should remain artistically consistent:

- Planet/region view: low-detail coherent terrain, roads, rivers, city silhouettes,
  settlement halos, and atmospheric haze.
- City view: districts, skyline silhouettes, roads, bridges, plazas, farms, docks,
  landmarks, and crowd aggregation.
- Street view: detailed buildings, citizens, units, paths, decals, and local terrain
  texture detail.

## Texture Library Plan

Current library:

- 48 CC0 Poly Haven diffuse textures in 512px mobile tier.
- Matching 48 CC0 Poly Haven diffuse textures in 2048px desktop/ultra tier.

Next texture additions should prioritize more semantic variants, not raw size:

- Grasslands: meadow, short grass, trampled grass.
- Forests: conifer floor, broadleaf floor, mossy roots.
- Mountains: scree, granite face, dark basalt.
- Deserts: dune sand, cracked clay, rocky desert.
- Wetlands: reeds/mud, peat, wet stones.
- Volcanic: basalt, cooled lava, ash field.

All additions must remain CC0, Public Domain, or MIT-compatible and be documented in
`ASSETS.md` and `ASSET_LICENSES.md`.

## Settings UI

Settings live in the God panel and are grouped by purpose:

- Graphics: Low, Medium, High, Ultra, RTX 4090 Ultra, Auto.
- Visual: biome detail intensity.
- Controls: camera sensitivity and inverted controls.
- World: speed default and UI scale.
- Debug: FPS, chunk borders, and LOD visualization.

## Phased Roadmap

1. Expand terrain material variety with additional CC0 biome-specific variants.
2. Add biome-aware natural props: rocks, reeds, shrubs, deadwood, snow drifts, and
   volcanic debris, all derived from terrain masks and markers.
3. Improve road and river art with more material variants, riverbank vegetation, and
   bridge silhouettes tied to real crossings.
4. Strengthen city district identity with palette rules, plaza shapes, dock clutter,
   farms, walls, and landmark silhouettes tied to city stats.
5. Add weather presentation from existing climate/event state: drought haze, rain
   shimmer, snow tint, ash haze near eruptions.
6. Create art QA views for each biome and each graphics preset.
