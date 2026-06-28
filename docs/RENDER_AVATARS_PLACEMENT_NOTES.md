# Renderer slice: character avatars + building overlap fix

Two localized rendering/projection changes. The simulation core is untouched.

## 1. Agents → small character-like avatars

**Before:** citizens drew as a bare `CapsuleGeometry` (a pill) and units as a
`ConeGeometry` (a cone) — readable as "a coloured dot", not a person.

**After:** both draw a low-poly **humanoid** (legs + torso + shoulders + head) built once
and merged into a single geometry, so the whole crowd is still ONE instanced draw call.
Units additionally carry a small standard/banner so a marching party reads differently
from a lone citizen.

- Variation is preserved and uses sim state: per-instance colour by **role/group**
  (`citizenColor` / `unitColor`), scale by **cohort** (`agentScale` / `unitScale`),
  facing by **movement direction** (`agentAngle`).
- Avatars stand **feet-on-terrain** (`AGENT_LIFT`), where the old primitives floated at a
  fixed +0.48 / +0.62 centre lift.
- **Fallback preserved:** the geometry is generated procedurally (no asset pipeline); if
  `BufferGeometryUtils` is unavailable the renderer's existing try/catch keeps the world
  rendering.

Files: `web/js/omega/RendererApp.js` — `humanoidGeometry()` (new), `buildCitizens`,
`buildUnits`, `updateAnimatedMeshes`, `unitPosition`.

## 2. Buildings stop stacking/overlapping

**Root cause (found by inspection + arithmetic):** placement was *already* collision-free
in **tile space** (`aeon/render/placement.py:layout_city`, deterministic, memoized, slot
packed per district). But the renderer sized each building with an **ad-hoc factor
unrelated to the footprint the layout reserved**. With a 192-tile world mapped to
`SCALE=100`, one tile ≈ 0.52 three-units; the drawn volumes came out ~2× the reserved
footprint, so neighbours overlapped even though their **centres** were correctly spaced.
Crowded slots were worse: the layout *shrinks* a slot's reserved radius to squeeze it in,
but the renderer still drew the full footprint.

**Fix (consistency, not jitter):** the drawn horizontal size is now locked to the
footprint the layout reserved.

- `projection.py` now exports, per building: `visual.footprint` = the layout's **reserved
  radius** (post-shrink), and `visual.spacing` = the city's min-spacing factor.
- `RendererApp.buildingScale()` sets rendered half-width = `footprint · tileScale · PACK`,
  where `PACK = min(1.1, spacing·0.85) ≤ spacing`. Since the layout guarantees centre gaps
  ≥ `(rA+rB)·spacing`, two slots can **never** render as overlapping volumes — at any
  spacing setting. Height stays free of the packing constraint, so civic/landmark
  structures still rise. Falls back to the legacy factor if footprint/layout data is
  missing (older chunks).

Placement remains **deterministic and stable** (all jitter from sha1 of stable ids,
memoized per city) — no per-frame randomness was added or relied upon.

Files: `aeon/render/projection.py` — `_layout_offset` (returns reserved radius),
`_building_record` (footprint/spacing fields); `web/js/omega/RendererApp.js` —
`buildingScale`/`legacyBuildingScale`, `buildingGeometry` (caches base half-width),
`buildBuildings` caller. Caller updated in `aeon/agents/spatial.py`.

## Verification

- `pytest tests/test_placement.py test_placement_density.py test_world_api.py
  test_spatial_embodiment.py test_interpret.py test_worldgen_believability.py` → **44
  passed**.
- **Rendered-volume overlap check** (reproduces the renderer math against real layouts
  across the whole spacing range 0.8–2.4): **0 overlaps**, building widths ~0.57–0.92
  three-units. Old behaviour produced systematic ~2× overlap.

### Visual before/after (manual)

```
source .venv/bin/activate
python -m aeon            # serve on :8080, then open in a browser
POST /api/speed {"speed":80}   # let cities grow; focus a city (🏙 City cam)
```

- Agents: zoom to a city — moving dots now read as little figures; traders/armies carry a
  standard. Toggle camera to **Unit** to follow one.
- Buildings: in a dense city the houses pack into tight, non-overlapping blocks with thin
  streets instead of stacking into a single mass. The **Debug Placement** toggle
  (`renderOptions.placementDebug`) red-tints any slot the layout genuinely couldn't place
  (overcrowded), which should be rare.
- Tuning knob: `min_building_distance` (Setup tab / params) trades street width vs density;
  buildings always stay non-overlapping because `PACK ≤ spacing`.
