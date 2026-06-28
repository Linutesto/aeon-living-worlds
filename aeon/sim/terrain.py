"""Terrain: elevation, oceans, mountains, rivers, caves, and biome classification.

`generate()` runs once at genesis. `step()` runs every tick and applies the slow
geological forces the governor can dial: tectonic drift and volcanic uplift, plus
sea-level changes that drown or expose coastline.

Heightmap is value-noise plus continent masks and ridge-chain uplift. Genesis also
computes compact spatial analysis layers used by city founding and road routing.
"""

from __future__ import annotations

import numpy as np

from . import world as _w

_BIG = 1_000_000.0


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
    p = world.params
    base = _value_noise(rng, h, w, octaves=6)
    detail = _value_noise(rng, h, w, octaves=4)
    continents = _continent_mask(rng, h, w)
    yy, xx = np.mgrid[0:h, 0:w]
    edge = np.maximum(np.abs((xx / max(1, w - 1)) * 2.0 - 1.0),
                      np.abs((yy / max(1, h - 1)) * 2.0 - 1.0))
    edge_falloff = np.clip((edge - 0.74) / 0.28, 0.0, 1.0) ** 1.6
    raw = 0.58 * continents + 0.32 * base + 0.10 * detail - 0.34 * edge_falloff

    target_land = float(np.clip(p.land_percent, 0.35, 0.78))
    sea_threshold = float(np.quantile(raw, 1.0 - target_land))
    elev = raw - sea_threshold

    ridge = _mountain_chains(rng, h, w)
    land_guess = elev > p.sea_level
    if land_guess.any():
        cutoff = np.quantile(ridge[land_guess], max(0.0, 1.0 - p.mountain_percent))
    else:
        cutoff = 0.75
    uplift = np.maximum(0.0, ridge - cutoff)
    if uplift.max() > 0:
        uplift = uplift / max(1e-6, float(uplift.max()))
    elev += uplift * (0.42 + 0.28 * p.mountain_percent / 0.11)
    elev -= _valley_field(ridge) * 0.05
    elev = _normalize_elevation(elev)
    world.elevation = elev.astype(np.float32)
    world.water = np.zeros((h, w), dtype=np.float32)
    _add_inland_lakes(world)
    _carve_rivers(world, int(round(p.river_count)))
    classify_biomes(world)
    analyze_terrain(world)


def _continent_mask(rng, h: int, w: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    y = (yy / max(1, h - 1)) * 2.0 - 1.0
    x = (xx / max(1, w - 1)) * 2.0 - 1.0
    mask = np.zeros((h, w), dtype=np.float32)
    n = int(rng.integers(2, 5))
    for i in range(n):
        cx = float(rng.uniform(-0.48, 0.48))
        cy = float(rng.uniform(-0.48, 0.48))
        sx = float(rng.uniform(0.34, 0.72))
        sy = float(rng.uniform(0.30, 0.68))
        ang = float(rng.uniform(0.0, np.pi))
        ca, sa = np.cos(ang), np.sin(ang)
        rx = (x - cx) * ca + (y - cy) * sa
        ry = -(x - cx) * sa + (y - cy) * ca
        blob = np.exp(-((rx / sx) ** 2 + (ry / sy) ** 2) * 1.55)
        mask += blob.astype(np.float32) * float(rng.uniform(0.75, 1.25))
    if mask.max() > 0:
        mask /= float(mask.max())
    return mask * 2.0 - 1.0


def _mountain_chains(rng, h: int, w: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    field = np.zeros((h, w), dtype=np.float32)
    n = int(rng.integers(2, 5))
    for _ in range(n):
        x0, y0 = float(rng.uniform(0, w - 1)), float(rng.uniform(0, h - 1))
        length = float(rng.uniform(min(h, w) * 0.35, min(h, w) * 0.85))
        angle = float(rng.uniform(0.0, np.pi))
        x1 = np.clip(x0 + np.cos(angle) * length, 0, w - 1)
        y1 = np.clip(y0 + np.sin(angle) * length, 0, h - 1)
        dx, dy = x1 - x0, y1 - y0
        d2 = dx * dx + dy * dy + 1e-6
        t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / d2, 0.0, 1.0)
        px = x0 + t * dx
        py = y0 + t * dy
        dist = np.sqrt((xx - px) ** 2 + (yy - py) ** 2)
        width = float(rng.uniform(3.0, 8.0)) * max(h, w) / 192.0
        ridge = np.exp(-(dist ** 2) / max(1e-6, 2.0 * width * width))
        field = np.maximum(field, ridge.astype(np.float32) * float(rng.uniform(0.75, 1.15)))
    field += _value_noise(rng, h, w, octaves=3) * 0.12
    return np.clip(field, 0.0, 1.0).astype(np.float32)


def _valley_field(ridge: np.ndarray) -> np.ndarray:
    padded = np.pad(ridge, 1, mode="edge")
    blur = (
        padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:]
        + 4 * padded[1:-1, 1:-1]
    ) / 8.0
    return np.clip(blur - ridge, 0.0, 1.0).astype(np.float32)


def _normalize_elevation(elev: np.ndarray) -> np.ndarray:
    lo = float(np.percentile(elev, 1))
    hi = float(np.percentile(elev, 99))
    if hi - lo < 1e-6:
        return np.zeros(elev.shape, dtype=np.float32)
    out = elev / max(abs(hi), abs(lo), 1e-6)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def _add_inland_lakes(world: "_w.WorldState") -> None:
    rng = world.rng.stream("lakes")
    h, w = world.height, world.width
    land = world.elevation > world.params.sea_level
    interior = land & (_distance_to_water(world.elevation <= world.params.sea_level) > 6)
    if not interior.any():
        return
    low = world.elevation < np.quantile(world.elevation[interior], 0.22)
    candidates = np.argwhere(interior & low)
    if len(candidates) == 0:
        return
    lake_count = int(rng.integers(2, 7))
    for y, x in candidates[rng.choice(len(candidates), size=min(lake_count, len(candidates)), replace=False)]:
        r = int(rng.integers(2, 5))
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        disk = yy * yy + xx * xx <= r * r
        y0, y1 = max(0, y - r), min(h, y + r + 1)
        x0, x1 = max(0, x - r), min(w, x + r + 1)
        sy0, sx0 = r - (y - y0), r - (x - x0)
        sub = disk[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
        lake = world.water[y0:y1, x0:x1]
        lake[sub] = np.maximum(lake[sub], 0.55)


def _carve_rivers(world: "_w.WorldState", n: int = 12) -> None:
    """Carve rivers from high terrain toward ocean or lakes using local downhill flow."""
    rng = world.rng.stream("rivers")
    h, w = world.height, world.width
    elev = world.elevation
    land = elev > world.params.sea_level
    if not land.any():
        return
    water_target = (elev <= world.params.sea_level) | (world.water > 0.45)
    dist_water = _distance_to_water(water_target)
    high = land & (elev >= np.quantile(elev[land], 0.72)) & (dist_water > 5)
    sources = np.argwhere(high)
    if len(sources) == 0:
        return
    chosen = sources[rng.choice(len(sources), size=min(max(1, n), len(sources)), replace=False)]
    for y0, x0 in chosen:
        y, x = int(y0), int(x0)
        seen: set[tuple[int, int]] = set()
        for step in range(h + w):
            seen.add((y, x))
            width = 0 if step < 8 else 1 if step < 80 else 2
            _paint_water(world, y, x, width, 0.42)
            if elev[y, x] <= world.params.sea_level or (world.water[y, x] > 0.5 and step > 6):
                break
            ny, nx = y, x
            best = float(elev[y, x] + dist_water[y, x] * 0.018)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    yy, xx = min(h - 1, max(0, y + dy)), min(w - 1, max(0, x + dx))
                    score = float(elev[yy, xx] + dist_water[yy, xx] * 0.018
                                  + (0.02 if (yy, xx) in seen else 0.0))
                    if score < best:
                        best, ny, nx = score, yy, xx
            if (ny, nx) == (y, x):
                break
            elev[ny, nx] = min(elev[ny, nx], elev[y, x] - 0.004)
            y, x = ny, nx


def _paint_water(world: "_w.WorldState", y: int, x: int, r: int, depth: float) -> None:
    y0, y1 = max(0, y - r), min(world.height, y + r + 1)
    x0, x1 = max(0, x - r), min(world.width, x + r + 1)
    world.water[y0:y1, x0:x1] = np.maximum(world.water[y0:y1, x0:x1], depth)


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
        wet = world.rainfall > (0.52 / max(0.2, world.params.forest_density))
        dry = world.rainfall < (0.33 * world.params.desert_frequency)
        cold = (world.temperature < -2) | (elev > world.params.snow_line)
        biome[land & hot & dry] = B["desert"]
        biome[land & wet & (elev < 0.55)] = B["forest"]
        biome[land & cold] = B["tundra"]
        biome[land & cold & (elev > world.params.snow_line)] = B["snow"]
        biome[land & wet & (elev <= sea + 0.06)] = B["swamp"]
    world.biome = biome


def analyze_terrain(world: "_w.WorldState") -> None:
    """Cache slope, water/mountain distance and buildable score for settlement logic."""
    elev = world.elevation
    gy, gx = np.gradient(elev)
    slope = np.sqrt(gx * gx + gy * gy).astype(np.float32)
    water_mask = (elev <= world.params.sea_level) | (world.water > 0.2)
    mountain_mask = elev > 0.55
    water_d = _distance_to_water(water_mask)
    mountain_d = _distance_to_water(mountain_mask)
    world.terrain_slope = slope
    world.water_distance = water_d.astype(np.float32)
    world.mountain_distance = mountain_d.astype(np.float32)
    if world.food is not None and world.minerals is not None:
        compute_buildable_score(world)


def compute_buildable_score(world: "_w.WorldState") -> None:
    land = world.elevation > world.params.sea_level
    slope = getattr(world, "terrain_slope", np.zeros_like(world.elevation))
    water_d = getattr(world, "water_distance", _distance_to_water(~land))
    mountain_d = getattr(world, "mountain_distance", np.zeros_like(world.elevation))
    water_score = np.exp(-((water_d - 4.0) ** 2) / 90.0)
    slope_score = np.clip(1.0 - slope * 7.5, 0.0, 1.0)
    mountain_score = np.clip(mountain_d / 8.0, 0.0, 1.0)
    food = np.nan_to_num(world.food, nan=0.0)
    minerals = np.nan_to_num(world.minerals, nan=0.0)
    forest = (world.biome == _w.BIOME["forest"]).astype(np.float32)
    resources = np.clip(food * 0.58 + minerals * 0.22 + forest * 0.14, 0.0, 1.0)
    temp_fit = np.ones_like(world.elevation, dtype=np.float32)
    if world.temperature is not None:
        temp_fit = np.exp(-((world.temperature - 18.0) ** 2) / 500.0).astype(np.float32)
    expansion = _open_land_score(land & (slope < 0.12))
    road = getattr(world, "road_access", None)
    if road is None:
        road = np.zeros_like(world.elevation)
    score = (
        0.26 * resources + 0.20 * water_score + 0.18 * slope_score
        + 0.12 * mountain_score + 0.14 * temp_fit + 0.08 * expansion + 0.02 * road
    )
    score = np.where(land & (slope < 0.24), score, 0.0)
    world.buildable_score = np.clip(score, 0.0, 1.0).astype(np.float32)


def _open_land_score(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask.astype(np.float32), 2, mode="edge")
    total = np.zeros(mask.shape, dtype=np.float32)
    for dy in range(5):
        for dx in range(5):
            total += padded[dy:dy + mask.shape[0], dx:dx + mask.shape[1]]
    return total / 25.0


def _distance_to_water(mask: np.ndarray) -> np.ndarray:
    dist = np.where(mask, 0.0, _BIG).astype(np.float32)
    h, w = dist.shape
    for y in range(h):
        row = dist[y]
        for x in range(w):
            best = row[x]
            if y > 0:
                best = min(best, dist[y - 1, x] + 1.0)
            if x > 0:
                best = min(best, row[x - 1] + 1.0)
            row[x] = best
    for y in range(h - 1, -1, -1):
        row = dist[y]
        for x in range(w - 1, -1, -1):
            best = row[x]
            if y + 1 < h:
                best = min(best, dist[y + 1, x] + 1.0)
            if x + 1 < w:
                best = min(best, row[x + 1] + 1.0)
            row[x] = best
    return dist


def validate_terrain(world: "_w.WorldState", *, min_cities: int | None = None) -> tuple[bool, dict]:
    land = world.elevation > world.params.sea_level
    land_ratio = float(land.mean())
    mountain_ratio = float(((world.elevation > 0.55) & land).sum() / max(1, land.sum()))
    buildable = getattr(world, "buildable_score", np.zeros_like(world.elevation))
    good_sites = int((buildable > 0.46).sum())
    rivers = int((world.water > 0.35).sum())
    largest = _largest_land_fraction(land)
    n_cities = min_cities if min_cities is not None else int(getattr(world.cfg.sim, "start_civilizations", 5))
    reasons: list[str] = []
    if land_ratio < 0.35:
        reasons.append("too_much_water")
    if land_ratio > 0.80:
        reasons.append("too_little_water")
    if largest < 0.56:
        reasons.append("continents_disconnected")
    if mountain_ratio > 0.34:
        reasons.append("excessive_mountains")
    if rivers < max(16, n_cities * 8):
        reasons.append("no_rivers")
    if good_sites < max(80, n_cities * 28):
        reasons.append("cities_cannot_fit")
    return not reasons, {
        "land_ratio": round(land_ratio, 4),
        "mountain_land_ratio": round(mountain_ratio, 4),
        "largest_landmass_fraction": round(largest, 4),
        "good_buildable_tiles": good_sites,
        "river_tiles": rivers,
        "reasons": reasons,
    }


def _largest_land_fraction(land: np.ndarray) -> float:
    seen = np.zeros(land.shape, dtype=bool)
    h, w = land.shape
    total = int(land.sum())
    if total <= 0:
        return 0.0
    best = 0
    for y0, x0 in np.argwhere(land):
        y0 = int(y0); x0 = int(x0)
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]
        seen[y0, x0] = True
        count = 0
        while stack:
            y, x = stack.pop()
            count += 1
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < h and 0 <= xx < w and land[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    stack.append((yy, xx))
        best = max(best, count)
    return best / max(1, total)


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
    analyze_terrain(world)


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
