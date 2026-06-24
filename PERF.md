# AEON Performance Architecture

Core rule: performance work must preserve simulation fidelity and visual truth. Lower
detail is acceptable; blank terrain, fake activity, and full-population payloads are not.

## Targets

- **RTX 4090 Ultra:** richer chunks, farther render distance, higher terrain and texture
  quality, dense instancing, 60 FPS target.
- **Desktop:** stable 60 FPS with high visual density.
- **Mobile:** degraded but usable; 30 FPS goal with fallback terrain always visible.

## Research-Backed Techniques

- **Geometry clipmaps / chunked LOD / CDLOD:** nested rings and shared height samples keep
  large terrain streamable. AEON already has global render height sampling; next step is
  clipmap-like ultra rings outside the Omega chunk window.
- **GPU-driven rendering / Hi-Z occlusion:** build a depth pyramid and reject hidden
  districts/buildings/vegetation before draw submission. This is later-stage WebGL2 work.
- **Data-oriented hot paths:** keep aggregate city/civ arrays for simulation and render
  dirty checks. Materialize individuals only for focused cities.
- **Instancing and impostors:** repeated city/building/vegetation/crowd shapes should be
  instanced by archetype/material; distant cities become impostor silhouettes.

## Current Performance Substrate

- Chunk streaming, LOD, fallback terrain, quality governor, shared material/texture caches.
- Resource and demographic systems are aggregate per city, not per citizen.
- Render payloads stay bounded by chunk, LOD, and city/building caps.

## Next Optimization Slices

1. **Dirty propagation.** Version city economy/demography/building/history state and avoid
   resending unchanged chunk payloads.
2. **Hierarchical culling.** Chunk → city → district → archetype bins. Skip whole bins by
   frustum, distance, overlay state, and quality preset.
3. **Instanced archetype bins.** Batch buildings by archetype/material/roof/condition, not
   one mesh per building.
4. **Distant city impostors.** Replace far building instances with skyline strips derived
   from city stats: wealth, population, education, faith, war, heritage, trauma.
5. **Route bundling.** Bundle roads/route overlays by purpose and importance before render.
   Default cinematic mode should hide low-importance overlays.
6. **Texture budget manager.** Texture quality is preset-driven: 512 mobile, 1K desktop,
   2K/4K only for ultra hero materials. Reuse atlases and shared materials.
7. **Update frequency split.** Camera every frame; nearby agents frequent; city lights and
   overlays medium; distant chunks rare; Atlas/dashboard throttled.
8. **Object pools.** Reuse matrices, colors, vectors, and mesh containers during rebuilds.
9. **Worker queues.** Async chunk builds with capped uploads per frame; old chunks remain
   visible until replacements are uploaded.
10. **GPU culling prototype.** Test Hi-Z/occlusion only after CPU-side culling and
    instancing are stable.

## Metrics To Keep Visible

- FPS and frame time p95.
- Draw calls, triangles, mesh count, instanced mesh count.
- Visible/loading/cached/stale chunks.
- Chunk build queue length, uploads per frame, build ms.
- Texture count and texture memory estimate.
- JS heap estimate where available.
- Current preset, DPR/render scale, quality-governor mode.
- Payload bytes per chunk and cache hit/miss rates.

## Non-Negotiables

- No full-world or full-population payloads.
- No disposal of shared materials/textures/geometries during chunk rebuild.
- No white/blank terrain while high-detail chunks stream.
- No per-frame material creation.
- No renderer-side invention of roads, cities, citizens, landmarks, scars, or lights.
