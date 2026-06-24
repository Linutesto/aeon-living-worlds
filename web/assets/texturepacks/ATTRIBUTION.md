# Texture Pack Attribution

AEON's texture packs are **deterministic recombinations of the existing CC0 albedo
library** in `web/assets/textures/`. No new image assets are downloaded — every pack is
defined purely by:

  * an albedo **remap** (e.g. snowy-ice-age maps `grass -> snow`),
  * color **grading** (exposure / fog tint / saturation),
  * **water** color + opacity, and
  * a **particle** theme.

## Source textures

All base textures in `web/assets/textures/*.jpg` are CC0 / public-domain
(ambientCG-style PBR albedo). They may be used, modified, and redistributed without
attribution; this file documents provenance as a courtesy. If you add a texture from an
external source, record its name, URL, author, and license below.

| texture | source | license |
|---------|--------|---------|
| (base library) | CC0 albedo library | CC0 1.0 |

## Packs

- **Default Clean** (`default-clean`) — The neutral baseline — true-to-source albedo, balanced grading.
- **Realistic Medieval** (`realistic-medieval`) — Warm, earthy stone-and-thatch towns under a low golden sun.
- **Snowy Ice Age** (`snowy-ice-age`) — A frozen world — snowfields, ice rivers, pale blue light.
- **Volcanic Ash** (`volcanic-ash`) — Scorched basalt and ashfall, embers glowing in a dark haze.
- **Lush Green** (`lush-green`) — Verdant overgrowth — deep grass, mossy stone, vivid foliage.
- **Desert Dry** (`desert-dry`) — Sun-baked dunes, cracked earth, and dust on a hot wind.
- **Dark Fantasy** (`dark-fantasy`) — A grim, desaturated realm of cold stone and brooding fog.
- **Performance (Low)** (`performance-low`) — Flat albedo, no heavy effects — maximum FPS for weak GPUs/mobile.
