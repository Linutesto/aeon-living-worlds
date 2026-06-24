# AEON Renderer Evolution Roadmap

Goal: every pixel should communicate simulation information. AEON should look like a
living civilization map, not debug geometry, while remaining streamable.

## Source Of Truth

Render records may derive from:
- terrain, climate, water, resources, and biome grids;
- cities, buildings, districts, units, and materialized focused citizens;
- society state: cultures, religions, factions;
- history and `world.historical_sites`;
- aggregate city/civ fields such as resources, demographics, tech, heritage, trauma.

Render records may not invent cities, citizens, roads, landmarks, lore, disasters, or
activity.

## Built In The World Perfection Slice

- Persistent historical sites appear in Omega chunk `scars`.
- City skylines expose education, urbanization, migration pressure, heritage, and trauma.
- District payloads include identity profiles from class, professions, resources, faith,
  knowledge, industry, memory, and damage.
- Road segments carry route purpose: local, trade, migration, military.
- City/civ payloads expose compact demographics and tech domains for overlays.

## Visual Information Layers

1. **Terrain:** biome, elevation, slope, fertility, water distance, snow, wetland, farms,
   scars, ruins, old roads.
2. **Cities:** wealth, poverty, population density, education, faith, militarization,
   industry, civic stability, famine/plague/war/unrest.
3. **Districts:** dominant identity plus density, prosperity, damage, material, activity.
4. **Roads/routes:** normal roads always subtle; trade/migration/military overlays selected
   or thresholded by importance.
5. **Night:** lights communicate wealth, density, knowledge, faith, unrest, plague/famine,
   and cultural identity.
6. **History:** battlefield/ruin/shrine/foundation/discovery markers age and fade, but do
   not vanish from world memory unless capped.

## Next Renderer Upgrades

1. **District identity materials.** Map `district.identity.dominant` to paving, roof,
   light, clutter, and silhouette rules.
2. **City impostors.** Far cities render as skyline strips derived from skyline stats and
   landmark justification.
3. **Route overlay cleanup.** Default route overlays off or important-only; selected routes
   can pulse, but normal mode stays cinematic.
4. **Historical scar rendering.** Use persistent site kind/intensity/age for ruins,
   battlefield darkening, abandoned farms, shrines, monuments, and discovery markers.
5. **Knowledge/culture overlays.** Use `tech_domains`, education, religion/culture shares,
   and contact routes for heatmaps.
6. **Ultra preset materials.** Higher texture resolution, anisotropy, shadows, denser
   instancing, atmospheric depth, and larger render distance.
7. **Mobile preset discipline.** Same truth, lower density: fallback terrain, silhouettes,
   capped citizens, simpler materials, reduced overlays.

## Visual QA Checklist

- Terrain opaque and continuous.
- Debug overlays off by default.
- Roads sit on terrain and do not dominate.
- Cities read as rich/poor/starving/educated/militarized/religious/unstable without UI.
- Night lighting changes information density, not just mood.
- No white flashes, blank chunks, or full-scene disappearance.
- Pixel viewport remains usable; RTX 4090 preset looks meaningfully better.
