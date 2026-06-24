# AEON Terrain Reforge Research

This pass adapts proven terrain-rendering ideas to AEON's existing chunk streaming
renderer. The references below are used for concepts only; no incompatible code is
copied.

## CDLOD / Quadtree LOD

- Reference: Filip Strugar, "Continuous Distance-Dependent Level of Detail for
  Rendering Heightmaps" — https://aggrobird.com/files/cdlod_latest.pdf
- Problem solved: stable camera-distance LOD over large heightmap terrain.
- Technique AEON borrows: choose terrain resolution by distance rings and keep the
  height source global so every sample of the same world coordinate is identical.
- License compatibility: paper/reference implementation concept only; no code copied.
- Fit for AEON: AEON already streams chunks by camera distance, so CDLOD's central
  idea maps to the current `desiredTerrainLod()` ladder without replacing the renderer.

## Chunked LOD Terrain

- Reference: Thatcher Ulrich, "Chunked LOD" — https://tulrich.com/geekstuff/chunklod.html
- Problem solved: rendering large terrain in chunk batches with GPU-friendly meshes.
- Technique AEON borrows: keep chunks as renderable batches, use shared materials,
  and hide cracks locally with edge skirts when neighbor-aware stitching is not yet
  worth the complexity.
- License compatibility: public article/concept only; no source copied.
- Fit for AEON: AEON's renderer already has chunk payloads, double-buffered swaps,
  LOD hysteresis, and persistent fallback terrain. Skirts are a small, compatible
  addition.

## Three.js CDLOD Example

- Reference: `felixpalmer/lod-terrain` — https://github.com/felixpalmer/lod-terrain
- Problem solved: camera-distance terrain LOD in Three.js.
- Technique AEON borrows: keep terrain material and geometry paths separated from
  app state, and make LOD selection a renderer concern instead of simulation state.
- License compatibility: repository is used as an architectural reference only; no
  code copied into AEON.
- Fit for AEON: reinforces AEON's split between Python simulation projection and
  JavaScript renderer LOD.

## THREE.Terrain

- Reference: `IceCreamYou/THREE.Terrain` — https://github.com/IceCreamYou/THREE.Terrain
- Problem solved: generating terrain meshes and materialized heightmap terrain in
  Three.js. The repository is MIT licensed.
- Technique AEON borrows: heightmap-driven mesh generation and texture/material
  separation, not procedural world generation.
- License compatibility: MIT, but no code copied; AEON uses its own simulation
  height data.
- Fit for AEON: useful confirmation that terrain geometry should be a deterministic
  function of height samples and shared materials.

## Crack/Seam Techniques

- Reference: terrain skirt discussion in chunked LOD literature and practical notes
  such as https://thedemonthrone.ca/projects/rendering-terrain/rendering-terrain-part-15-skirts-and-other-additions/
- Problem solved: sky gaps and cracks between separately rendered terrain chunks.
- Technique AEON borrows: add downward edge skirts as a conservative fallback while
  keeping shared boundary height samples as the primary solution.
- License compatibility: concept only; no code copied.
- Fit for AEON: skirts are local, cheap, and work with the existing chunk mesh builder.

## Implementation Direction

AEON should not become a full quadtree terrain engine in this pass. The useful
adaptation is:

- One deterministic world-height sampler in the renderer.
- Chunk geometry samples all vertices through that sampler.
- Persistent low-detail fallback remains underneath, with high-detail chunks streamed
  on top.
- Chunk skirts seal cracks and protect against transient missing neighbors.
- Debug settings expose chunk borders and LOD tinting only when explicitly enabled.
