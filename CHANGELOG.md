# Changelog

All notable changes to AEON: Living Worlds are documented here. This project is an
experimental prototype; versions are informal and the format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Restart / New-World system** — restart from zero, same seed, random seed, or a custom
  config; reset a single layer (civilization / terrain-climate / cities-population /
  minds). Deterministic for a given seed.
- **Editable world-generation variables** — a validated `WorldGenConfig` (seed, map size,
  starting species/population/civilizations, climate/water/resource/war/tech knobs, and
  render budgets) exposed through a **Setup** UI panel and REST API.
- **Selectable texture packs** — 8 themes (default-clean, realistic-medieval,
  snowy-ice-age, volcanic-ash, lush-green, desert-dry, dark-fantasy, performance-low)
  built as deterministic remaps of the bundled CC0 library, with live in-renderer
  switching and per-pack color grading.
- **Collision-free city layout** — a deterministic, memoized building placement pass
  (spatial-hash rejection + footprint radii + road clearance + graceful fallback) plus a
  debug-overlay toggle.
- REST endpoints: `/api/world/config[/schema]`, `/api/world/restart[/random]`,
  `/api/world/reset-layer`, `/api/graphics/preset(s)`, `/api/texture-pack(s)`.
- Public-release docs, example configs, and packaging hygiene.

### Changed
- **Species-mind training reworked** to stop behavior drift: per-species policies now use
  Advantage-Weighted Regression (normalized advantages + entropy floor + per-update KL
  trust region) instead of off-policy REINFORCE over a large replay buffer. `/api/mind`
  now reports `drift` and `entropy`.
- Save files are versioned (v2) and carry the full generation config, graphics preset,
  texture pack, render budgets, and restart metadata. Older saves load with defaults.

## [0.1.0]

- Initial deterministic world (terrain, climate, ecology, cities, units, events).
- Plural civilizations with distinct archetypes and a lifecycle (collapse / merge /
  split / golden age).
- Emergent society: religions, factions, cultures, and an LLM-written Chronicle.
- Real-time Three.js/WebGL renderer with day/night, instancing, LOD, and quality presets.
- Local-LLM world-spirit (optional) via Ollama, with deterministic offline fallbacks.
