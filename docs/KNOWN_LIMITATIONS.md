# Known Limitations

AEON is an **experimental prototype**, not a finished game. Setting expectations honestly:

## Simulation
- **Sim balance is experimental.** Economy, demographics, war, famine, and migration are
  tuned by feel, not for fairness or long-run stability. Worlds can stagnate, runaway, or
  collapse in lopsided ways. Different seeds vary a lot.
- **Emergence is uneven.** Religions/factions/cultures emerge from real state, but whether
  a run produces a great story is not guaranteed — some worlds are quiet.

## Rendering & placement
- **Building overlap can still happen in dense or constrained terrain.** The placement
  pass avoids overlaps and roads with footprint-aware collision rejection, but under
  extreme overcrowding it falls back to shrinking/pushing buildings and, as a last resort,
  flags a building rather than guaranteeing a perfect non-overlapping layout. Use the
  **Debug Placement** toggle to see flagged cases.
- **Performance depends on your browser and GPU.** Target is ~60 FPS on a desktop GPU and
  ~30 FPS on mobile, but dense cities, high zoom, and the `ultra` presets can drop frames.
  Use the quality presets, the `performance-low` pack, and the perf HUD (`P`) to tune.
- **The renderer needs internet on first load** (Three.js loads from a CDN via an import
  map) unless you vendor the modules locally.

## Texture packs
- **Texture packs are deterministic remaps + color grading of the bundled CC0 library**,
  not bespoke per-pack art. They change the *theme* (which textures and grading are used),
  not the underlying asset set. Fully custom per-pack assets are planned, not done.
- The high-resolution 2K texture set is not committed (repo-size tradeoff); `ultra`
  presets fall back to base-resolution textures when it's absent.

## AI / minds
- **The AI systems are prototypes.** Per-species policies (Advantage-Weighted Regression)
  and the teacher→student "society mind" are research-grade, not polished gameplay AI.
  They influence flavor and tendencies more than they "win" anything.
- **The LLM world-spirit is optional and best-effort.** With no Ollama server, the world
  runs on deterministic fallbacks; narration/interviews are then placeholders. LLM output
  quality depends entirely on the local model you point it at.

## Saves & config
- **Save compatibility may change.** Saves are versioned and old ones load with defaults,
  but during active development the format can shift; don't rely on long-term save
  durability yet.

## UI / platform
- **Mobile UI is usable but still evolving.** It's built mobile-first (Pixel-class phone in
  portrait), but some panels are dense on small screens.
- **No authentication.** The server and its world-mutating APIs are unauthenticated by
  design for single-user local use — don't expose the port publicly (see
  [../SECURITY.md](../SECURITY.md)).

## Scope
- **This is not a finished game.** There are no goals, win/lose conditions, tutorials, or
  long-term progression yet. It's a living-world sandbox you observe and steer.
