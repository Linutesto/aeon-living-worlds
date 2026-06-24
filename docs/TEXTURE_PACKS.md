# Texture Packs

Texture packs are switchable visual themes for the world. Pick one in the **Setup →
Graphics** panel, or via `POST /api/texture-pack {"pack": "<name>"}`. The change is applied
live (the renderer re-themes the terrain and rebuilds material caches without a reload).

## Available packs

| Pack | Look |
|---|---|
| `default-clean` | Neutral baseline — true-to-source albedo, balanced grading |
| `realistic-medieval` | Warm, earthy stone-and-thatch towns under a low golden sun |
| `snowy-ice-age` | Frozen world — snowfields, ice rivers, pale blue light |
| `volcanic-ash` | Scorched basalt and ashfall in a dark haze |
| `lush-green` | Verdant overgrowth — deep grass, mossy stone, vivid foliage |
| `desert-dry` | Sun-baked dunes, cracked earth, dust on a hot wind |
| `dark-fantasy` | Grim, desaturated realm of cold stone and brooding fog |
| `performance-low` | Flat albedo, minimal effects — maximum FPS for weak GPUs/mobile |

## How packs work

Each pack is a small manifest at `web/assets/texturepacks/<name>/pack.json`:

```jsonc
{
  "name": "snowy-ice-age",
  "remap":  { "grass": "snow", "mud": "ice", ... },   // albedo slot → texture
  "grade":  { "exposure": 1.1, "fog": "#d6e6f2", "saturation": 0.85 },
  "water":  { "color": "#8fb6cf", "opacity": 0.7 },
  "particles": "snow"
}
```

Packs are **deterministic recombinations of the bundled CC0 texture library** — they remap
which albedo each terrain/building slot uses and apply color grading, rather than shipping
separate full asset sets. This keeps the repo lean and every pack legally clean (CC0). The
renderer keeps the raw loaded textures in one table and exposes a pack-remapped *view*, so
a swap re-themes terrain and buildings instantly.

> Note: this means packs are **theme remaps**, not bespoke per-pack art. Dropping in fully
> custom texture sets per pack is on the roadmap — see
> [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Adding your own pack

1. Create `web/assets/texturepacks/<your-pack>/pack.json` following the schema above
   (reuse texture names from `web/assets/textures/`).
2. Add `<your-pack>` to `TEXTURE_PACKS` in `aeon/sim/worldgen.py` and to the renderer's
   pack list so it appears in the selector and validates server-side.
3. If you add **new** image files, keep them CC0/public-domain and record their source in
   `web/assets/texturepacks/ATTRIBUTION.md`.

## Attribution

The base textures are CC0 / public-domain. See
[`web/assets/texturepacks/ATTRIBUTION.md`](../web/assets/texturepacks/ATTRIBUTION.md) and
[ASSET_LICENSES.md](ASSET_LICENSES.md).

## High-resolution textures

A 2K texture variant set (`web/assets/textures/2k/`, ~130 MB) is **not committed** to keep
the repo small; the renderer falls back to the base-resolution textures when it's absent.
Only the `ultra` / `rtx-4090-ultra` presets request 2K — every other preset uses the
bundled base textures and looks correct out of the box.
