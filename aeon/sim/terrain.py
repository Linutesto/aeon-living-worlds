"""Terrain: elevation, oceans, mountains, rivers, caves, and biome classification.

`generate()` runs once at genesis. `step()` runs every tick and applies the slow
geological forces the governor can dial: tectonic drift and volcanic uplift, plus
sea-level changes that drown or expose coastline.

Heightmap is value-noise via summed octaves — cheap and deterministic. Real fractal
terrain, hydraulic river carving, and cave networks are the depth to add later.
"""

from __future__ import annotations

import numpy as np

from . import world as _w


def _value_noise(rng, h: int, w: int, octaves: int = 5) -> np.ndarray:
    """Summed-octave value noise in roughly [-1, 1]."""
    field = np.zeros((h, w), dtype=np.float32)
    amp = 1.0
    total = 0.0
    for o in range(octaves):
        step = max(1, 2 ** (octaves - o))
        gh, gw = h // step + 2, w // step + 2
        grid = rng.standard_normal((gh, gw)).astype(np.float32)
        # bilinear upscale to full resolution
        ys = np.linspace(0, gh - 1, h)
        xs = np.linspace(0, gw - 1, w)
        y0 = np.floor(ys).astype(int); x0 = np.floor(xs).astype(int)
        y1 = np.minimum(y0 + 1, gh - 1); x1 = np.minimum(x0 + 1, gw - 1)
        fy = (ys - y0)[:, None]; fx = (xs - x0)[None, :]
        top = grid[y0][:, x0] * (1 - fx) + grid[y0][:, x1] * fx
        bot = grid[y1][:, x0] * (1 - fx) + grid[y1][:, x1] * fx
        field += amp * (top * (1 - fy) + bot * fy)
        total += amp
        amp *= 0.5
    return field / total


def generate(world: "_w.WorldState") -> None:
    rng = world.rng.stream("terrain")
    h, w = world.height, world.width
    noise = _value_noise(rng, h, w, octaves=6)
    # bias toward a central landmass ringed by ocean
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = h / 2, w / 2
    radial = 1.0 - (((yy - cy) / (h / 2)) ** 2 + ((xx - cx) / (w / 2)) ** 2)
    elev = (0.6 * noise + 0.4 * radial).astype(np.float32)
    # Calm landform: keep the bulk of the land as gentle low plains (capped flat),
    # and let only the rare high-noise spikes rise into a few real mountains. This
    # gives "mostly flat with a couple of mountains" rather than uniform lumpiness.
    plains = np.clip(elev, -1.0, 0.18)
    peaks = np.maximum(0.0, elev - 0.5) * 1.7      # only the highest noise → mountains
    elev = plains + peaks
    world.elevation = np.clip(elev, -1, 1).astype(np.float32)
    world.water = np.zeros((h, w), dtype=np.float32)
    _carve_rivers(world)
    classify_biomes(world)


def _carve_rivers(world: "_w.WorldState", n: int = 12) -> None:
    """Greedy downhill walks from high points. Placeholder for real hydrology."""
    rng = world.rng.stream("rivers")
    h, w = world.height, world.width
    elev = world.elevation
    for _ in range(n):
        y, x = int(rng.integers(0, h)), int(rng.integers(0, w))
        for _ in range(h + w):
            world.water[y, x] = max(world.water[y, x], 0.4)
            ny, nx = y, x
            best = elev[y, x]
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = (y + dy) % h, (x + dx) % w
                    if elev[yy, xx] < best:
                        best, ny, nx = elev[yy, xx], yy, xx
            if (ny, nx) == (y, x) or elev[ny, nx] < world.params.sea_level:
                break
            y, x = ny, nx


def classify_biomes(world: "_w.WorldState") -> None:
    """Assign biome ids from elevation + (if present) climate."""
    B = _w.BIOME
    elev = world.elevation
    sea = world.params.sea_level
    biome = np.full(elev.shape, B["grassland"], dtype=np.int8)
    biome[elev <= sea] = B["ocean"]
    biome[(elev > sea) & (elev <= sea + 0.03)] = B["beach"]
    biome[elev > 0.55] = B["mountain"]
    biome[elev > 0.8] = B["snow"]
    if world.temperature is not None and world.rainfall is not None:
        land = elev > sea
        hot = world.temperature > 30
        wet = world.rainfall > 0.5
        cold = world.temperature < -2
        biome[land & hot & ~wet] = B["desert"]
        biome[land & wet & (elev < 0.55)] = B["forest"]
        biome[land & cold] = B["tundra"]
        biome[land & wet & (elev <= sea + 0.06)] = B["swamp"]
    world.biome = biome


def step(world: "_w.WorldState") -> None:
    p = world.params
    if p.tectonic_drift > 0:
        rng = world.rng.stream("tectonics")
        # rare, tiny, smooth uplift/subsidence
        if rng.random() < 0.05 * p.tectonic_drift:
            drift = _value_noise(rng, world.height, world.width, octaves=2)
            world.elevation = np.clip(
                world.elevation + 0.01 * p.tectonic_drift * drift, -1, 1
            ).astype(np.float32)
    if p.volcanic_activity > 0 and world.rng.chance("volcano", 0.02 * p.volcanic_activity):
        _erupt(world)
    classify_biomes(world)


def _erupt(world: "_w.WorldState") -> None:
    """Localized volcanic uplift — births new mountains/islands."""
    rng = world.rng.stream("volcano")
    h, w = world.height, world.width
    cy, cx = int(rng.integers(0, h)), int(rng.integers(0, w))
    r = int(rng.integers(3, 8))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    blob = np.exp(-(yy ** 2 + xx ** 2) / (2 * (r / 2) ** 2)).astype(np.float32)
    for (dy, dx), v in np.ndenumerate(blob):
        world.elevation[(cy + dy - r) % h, (cx + dx - r) % w] += 0.4 * v
    np.clip(world.elevation, -1, 1, out=world.elevation)
    world.add_marker("volcano", cy, cx, ttl=160, label="eruption")
