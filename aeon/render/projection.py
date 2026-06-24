"""Simulation-derived Omega renderer payloads.

The client asks for chunks around the camera. Each chunk contains only facts that
already exist in simulation state: terrain grids, city/district/building records,
materialized citizens, units, active markers, history scars, and policy pressures.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from . import placement as _placement

CHUNK_TILES = 32


def _global_heightmap(w, res: int = 129) -> dict[str, Any]:
    """ONE authoritative, globally-smoothed elevation field sampled by every renderer
    system (terrain mesh, buildings, roads). Because all chunks bilinear-sample this
    single grid by world position, shared boundary vertices read identical heights →
    no per-chunk smoothing divergence, no plates/seams (CDLOD heightmap pattern)."""
    smooth = _smooth2d(w.elevation, passes=2)
    ys = np.linspace(0, w.height - 1, res).astype(int)
    xs = np.linspace(0, w.width - 1, res).astype(int)
    grid = smooth[np.ix_(ys, xs)]
    return {"res": res, "sea_level": float(w.params.sea_level),
            "data": [round(float(v), 4) for v in grid.flatten()]}


def manifest_payload(engine) -> dict[str, Any]:
    w = engine.world
    return {
        "type": "omega_manifest",
        "tick": w.tick,
        "world": {"name": w.cfg.world.name, "seed": w.cfg.world.seed,
                  "width": w.width, "height": w.height},
        "heightmap": _global_heightmap(w),
        "chunk_tiles": CHUNK_TILES,
        "chunks": {"x": math.ceil(w.width / CHUNK_TILES),
                   "y": math.ceil(w.height / CHUNK_TILES)},
        "profiles": {
            "desktop": {"target_fps": 60, "max_buildings": 18000,
                        "max_agents": 2500, "terrain_lod": 1},
            "mobile": {"target_fps": 60, "max_buildings": 3500,
                       "max_agents": 320, "terrain_lod": 3},
        },
        "overlays": [
            "political", "economy", "population", "religion", "faction", "migration",
            "war", "climate", "resources", "policy_confidence",
            "rebellion_probability",
        ],
        "policy": engine.world.species_brain.status(),
    }


def chunk_payload(engine, cx: int, cy: int, lod: int = 1) -> dict[str, Any]:
    w = engine.world
    lod = max(1, min(5, int(lod)))
    x0 = max(0, int(cx) * CHUNK_TILES)
    y0 = max(0, int(cy) * CHUNK_TILES)
    # +1 tile of overlap so a chunk's far edge shares the exact boundary vertex with
    # its neighbour's near edge (same world tile, same elevation) → continuous surface,
    # no inter-chunk step/gap. LODs are restricted client-side to {1,2,4} that divide
    # CHUNK_TILES so the shared vertex always lands on a sample at every detail level.
    x1 = min(w.width, x0 + CHUNK_TILES + 1)
    y1 = min(w.height, y0 + CHUNK_TILES + 1)
    if x0 >= w.width or y0 >= w.height:
        return {"type": "omega_chunk", "chunk": [cx, cy], "empty": True}
    bounds = (x0, y0, x1, y1)
    rivers = _rivers(w, bounds, lod)
    roads = _roads(w, bounds)
    return {
        "type": "omega_chunk",
        "tick": w.tick,
        "chunk": [cx, cy],
        "lod": lod,
        "bounds": {"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                   "world_w": w.width, "world_h": w.height},
        "terrain": _terrain(w, bounds, lod),
        "features": _terrain_features(engine, bounds, lod),
        "rivers": rivers,
        "roads": roads,
        "bridges": _bridges(w, bounds, roads),
        "shorelines": _shorelines(w, bounds, lod),
        "districts": _districts(engine, bounds),
        "buildings": _buildings(engine, bounds, lod),
        "skylines": _skylines(engine, bounds),
        "citizens": _citizens(engine, bounds, lod),
        "units": _units(w, bounds, lod),
        "scars": _scars(engine, bounds),
        "overlays": _overlays(engine, bounds),
    }


def entity_payload(engine, entity_id: str) -> dict[str, Any] | None:
    if entity_id.startswith("person:"):
        try:
            pid = int(entity_id.split(":", 1)[1])
        except ValueError:
            return None
        p = engine.population.get(pid)
        return {"kind": "person", "entity_id": entity_id,
                "data": engine.inspect_person(pid)} if p else None
    if entity_id.startswith("building:"):
        bid = entity_id.split(":", 1)[1]
    else:
        bid = entity_id
    for city in engine.world.cities.values():
        b = getattr(city, "building_entities", {}).get(bid)
        if b:
            return {"kind": "building", "entity_id": f"building:{bid}",
                    "data": _building_record(engine, city, b, high_detail=True)}
    return None


def policy_inspector(engine) -> dict[str, Any]:
    brain = engine.world.species_brain
    replay = getattr(brain, "replay", [])
    actions = Counter(s.get("action", "unknown") for s in replay)
    kinds = Counter(s.get("kind", "unknown") for s in replay)
    rewards = [float(s.get("reward", 0.0)) for s in replay[-512:]]
    by_species: dict[int, list[float]] = defaultdict(list)
    for s in replay[-2000:]:
        by_species[int(s.get("species_id", 0))].append(float(s.get("reward", 0.0)))
    return {
        "type": "policy_inspector",
        "status": brain.status(),
        "samples": len(replay),
        "action_distribution": dict(actions.most_common()),
        "sample_kinds": dict(kinds.most_common(16)),
        "recent_reward_mean": round(float(np.mean(rewards)), 4) if rewards else 0.0,
        "recent_reward_min": round(float(np.min(rewards)), 4) if rewards else 0.0,
        "recent_reward_max": round(float(np.max(rewards)), 4) if rewards else 0.0,
        "species_rewards": {sid: round(float(np.mean(vals)), 4)
                            for sid, vals in by_species.items() if vals},
        "behavior_delta": brain.status().get("behavior_delta", {}),
        "recent_samples": replay[-24:],
    }


def policy_counterfactual(engine, city_id: int, remove: str = "") -> dict[str, Any] | None:
    city = engine.world.cities.get(city_id)
    if not city or not city.alive:
        return None
    base = _policy_pressure_for_city(engine, city)
    altered = dict(base)
    if remove == "food_scarcity":
        altered["migration"] = max(0.0, altered["migration"] - 0.45)
        altered["rebellion"] = max(0.0, altered["rebellion"] - 0.25)
        altered["cooperation"] = min(1.0, altered["cooperation"] + 0.18)
    elif remove == "religion":
        altered["religious_openness"] = min(1.0, altered["religious_openness"] + 0.25)
        altered["faction"] = min(1.0, altered["faction"] + 0.08)
    elif remove == "unrest":
        altered["rebellion"] = max(0.0, altered["rebellion"] - 0.5)
        altered["aggression"] = max(0.0, altered["aggression"] - 0.25)
    return {
        "type": "policy_counterfactual",
        "city_id": city.id,
        "city": city.name,
        "remove": remove or "none",
        "baseline": base,
        "counterfactual": altered,
        "explanation": _counterfactual_explanation(remove),
    }


def _terrain(w, bounds, lod: int) -> dict[str, Any]:
    x0, y0, x1, y1 = bounds
    step = lod
    sl = (slice(y0, y1, step), slice(x0, x1, step))
    elev = w.elevation[sl]
    derived = _terrain_visual_fields(w, bounds, step)
    return {
        "w": int(elev.shape[1]), "h": int(elev.shape[0]),
        "step": step,
        "sea_level": float(w.params.sea_level),
        "elevation": _flat(w.elevation[sl], 3),
        "smoothed_height": _flat(derived["smoothed_height"], 3),
        "slope": _flat(derived["slope"], 3),
        "cliff_mask": _flat(derived["cliff_mask"], 3),
        "beach_mask": _flat(derived["beach_mask"], 3),
        "snow_mask": _flat(derived["snow_mask"], 3),
        "riverbank_mask": _flat(derived["riverbank_mask"], 3),
        "wetland_mask": _flat(derived["wetland_mask"], 3),
        "farmland_visual_zone": _flat(derived["farmland_visual_zone"], 3),
        "moss_mask": _flat(derived["moss_mask"], 3),
        "volcanic_mask": _flat(derived["volcanic_mask"], 3),
        "road_suitability": _flat(derived["road_suitability"], 3),
        "settlement_visual_zone": _flat(derived["settlement_visual_zone"], 3),
        "biome": w.biome[sl].astype(int).flatten().tolist(),
        "water": _flat(w.water[sl], 3),
        "rainfall": _flat(w.rainfall[sl], 3),
        "temperature": _flat(w.temperature[sl], 2),
        "fertility": _flat(w.food[sl], 3),
        "minerals": _flat(w.minerals[sl], 3),
    }


def _terrain_visual_fields(w, bounds, step: int) -> dict[str, np.ndarray]:
    """Render-only derived geography. Does not mutate simulation state."""
    x0, y0, x1, y1 = bounds
    # Two smoothing passes plus gradient sampling need a wider global halo; otherwise
    # the same coordinate can get a slightly different slope near chunk edges.
    pad = max(6, step * 4)
    px0, py0 = max(0, x0 - pad), max(0, y0 - pad)
    px1, py1 = min(w.width, x1 + pad), min(w.height, y1 + pad)
    elev = w.elevation[py0:py1, px0:px1]
    smooth = _smooth2d(elev, passes=2)
    cy0, cy1 = y0 - py0, y1 - py0
    cx0, cx1 = x0 - px0, x1 - px0
    h = smooth[cy0:cy1:step, cx0:cx1:step]
    gy, gx = np.gradient(smooth)
    slope = np.sqrt(gx * gx + gy * gy)[cy0:cy1:step, cx0:cx1:step]
    biome = w.biome[y0:y1:step, x0:x1:step]
    water = w.water[y0:y1:step, x0:x1:step]
    temp = w.temperature[y0:y1:step, x0:x1:step]
    food = w.food[y0:y1:step, x0:x1:step]
    land = w.land_mask[y0:y1:step, x0:x1:step]
    sea = float(w.params.sea_level)
    cliff = np.clip((slope - 0.035) / 0.09, 0, 1)
    beach = np.clip((0.13 - np.abs(h - sea)) / 0.08, 0, 1) * land.astype(float)
    beach = np.maximum(beach, (biome == 1).astype(float))
    snow = np.clip((h - 0.62) / 0.24, 0, 1) * np.clip((-temp + 4) / 24, 0, 1)
    snow = np.maximum(snow, (biome == 6).astype(float))
    riverbank = np.clip((water - 0.12) / 0.45, 0, 1) * land.astype(float)
    wetland = np.maximum((biome == 7).astype(float),
                         np.clip((water + food - 0.72) / 0.65, 0, 1) * land.astype(float))
    moss = np.clip((food + water + np.clip(h - 0.24, 0, 1) - 0.82) / 0.7, 0, 1)
    moss *= land.astype(float) * (biome != 4).astype(float)
    road_suitability = np.clip(1.0 - slope * 7.5 - water * 1.2, 0, 1) * land.astype(float)
    settlement = np.zeros_like(h, dtype=float)
    farmland = np.zeros_like(h, dtype=float)
    volcanic = np.zeros_like(h, dtype=float)
    yy_grid = np.arange(y0, y1, step)[:, None]
    xx_grid = np.arange(x0, x1, step)[None, :]
    for city in _cities_in_bounds(w, bounds, pad=10):
        d = np.sqrt((yy_grid - city.pos[0]) ** 2 + (xx_grid - city.pos[1]) ** 2)
        radius = max(3.0, min(18.0, city.influence_radius * 0.35))
        settlement = np.maximum(settlement, np.clip(1.0 - d / radius, 0, 1))
        farms = int(getattr(city, "buildings", {}).get("farms", 0))
        if farms:
            farm_radius = max(3.5, min(22.0, math.sqrt(farms) * 1.6))
            fertile = np.clip((food - 0.22) / 0.6, 0, 1)
            farmland = np.maximum(farmland, np.clip(1.0 - d / farm_radius, 0, 1) * fertile)
    for m in getattr(w, "markers", []):
        if m.get("kind") not in {"volcano", "meteor"}:
            continue
        my, mx = float(m.get("y", -9999)), float(m.get("x", -9999))
        if not _point_in_bounds(bounds, my, mx, pad=16):
            continue
        d = np.sqrt((yy_grid - my) ** 2 + (xx_grid - mx) ** 2)
        volcanic = np.maximum(volcanic, np.clip(1.0 - d / 13.0, 0, 1) * land.astype(float))
    return {
        "smoothed_height": h,
        "slope": slope,
        "cliff_mask": cliff,
        "beach_mask": beach,
        "snow_mask": snow,
        "riverbank_mask": riverbank,
        "wetland_mask": wetland,
        "farmland_visual_zone": farmland,
        "moss_mask": moss,
        "volcanic_mask": volcanic,
        "road_suitability": road_suitability,
        "settlement_visual_zone": settlement,
    }


def _smooth2d(arr: np.ndarray, passes: int = 1) -> np.ndarray:
    out = arr.astype(float, copy=True)
    for _ in range(passes):
        p = np.pad(out, 1, mode="edge")
        out = (
            p[:-2, :-2] + p[:-2, 1:-1] * 2 + p[:-2, 2:] +
            p[1:-1, :-2] * 2 + p[1:-1, 1:-1] * 4 + p[1:-1, 2:] * 2 +
            p[2:, :-2] + p[2:, 1:-1] * 2 + p[2:, 2:]
        ) / 16.0
    return out


def _rivers(w, bounds, lod: int) -> list[list[float]]:
    x0, y0, x1, y1 = bounds
    lines: list[list[float]] = []
    step = max(1, lod)
    for y in range(y0 + 1, y1 - 1, step):
        for x in range(x0 + 1, x1 - 1, step):
            water = float(w.water[y, x])
            if water <= 0.22 or not w.land_mask[y, x]:
                continue
            nbrs = [(y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)]
            ny, nx = min(nbrs, key=lambda p: w.elevation[p])
            if float(w.water[ny, nx]) > 0.12:
                lines.append([x / w.width, y / w.height,
                              nx / w.width, ny / w.height,
                              min(1.0, water)])
    return lines[:900]


def _shorelines(w, bounds, lod: int) -> list[list[float]]:
    """Ocean/land edge segments derived directly from biome cells."""
    x0, y0, x1, y1 = bounds
    ocean = 0
    step = max(1, lod)
    out: list[list[float]] = []
    for y in range(y0, y1 - 1, step):
        for x in range(x0, x1 - 1, step):
            here = int(w.biome[y, x]) == ocean
            right = int(w.biome[y, x + 1]) == ocean
            down = int(w.biome[y + 1, x]) == ocean
            if here != right:
                out.append([(x + 0.5) / w.width, y / w.height,
                            (x + 0.5) / w.width, (y + step) / w.height,
                            round(float(w.water[y, x]), 3)])
            if here != down:
                out.append([x / w.width, (y + 0.5) / w.height,
                            (x + step) / w.width, (y + 0.5) / w.height,
                            round(float(w.water[y, x]), 3)])
            if len(out) >= 520:
                return out
    return out


def _bridges(w, bounds, roads: list[list[float]]) -> list[dict[str, Any]]:
    """Bridge hints where a real inter-city road crosses real river water."""
    out: list[dict[str, Any]] = []
    for road in roads:
        flat = np.asarray(road, dtype=object).ravel().tolist()
        if len(flat) < 6:
            continue
        x1, y1, x2, y2, _civ_id, traffic = flat[:6]
        x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
        traffic = float(traffic)
        samples = max(8, int((abs(x2 - x1) * w.width + abs(y2 - y1) * w.height) * 0.9))
        in_water = False
        start = 0
        for i in range(samples + 1):
            t = i / samples
            nx = x1 + (x2 - x1) * t
            ny = y1 + (y2 - y1) * t
            tx = max(0, min(w.width - 1, int(nx * w.width)))
            ty = max(0, min(w.height - 1, int(ny * w.height)))
            river = bool(w.land_mask[ty, tx] and float(w.water[ty, tx]) > 0.22)
            if river and not in_water:
                start = i
                in_water = True
            if in_water and (not river or i == samples):
                end = i
                mid = (start + end) / (2 * samples)
                mx = x1 + (x2 - x1) * mid
                my = y1 + (y2 - y1) * mid
                if _point_in_bounds(bounds, my * w.height, mx * w.width, pad=2):
                    out.append({
                        "x": round(mx, 5), "y": round(my, 5),
                        "angle": round(math.atan2(x2 - x1, y1 - y2), 4),
                        "length": round(max(0.55, (end - start + 2) / samples * 6), 3),
                        "traffic": round(float(traffic), 3),
                    })
                    if len(out) >= 80:
                        return out
                in_water = False
    return out


def _terrain_features(engine, bounds, lod: int) -> dict[str, list[dict[str, Any]]]:
    w = engine.world
    x0, y0, x1, y1 = bounds
    step = max(2, lod * 3)
    forests, farms, mines, snow = [], [], [], []
    for y in range(y0 + 1, y1 - 1, step):
        for x in range(x0 + 1, x1 - 1, step):
            biome = int(w.biome[y, x])
            if biome == 3 and len(forests) < 180:
                forests.append({"x": x / w.width, "y": y / w.height,
                                "density": round(float(w.food[y, x]), 3)})
            if biome == 6 and len(snow) < 120:
                snow.append({"x": x / w.width, "y": y / w.height,
                             "depth": round(max(0.1, float(w.elevation[y, x])), 3)})
    for city in _cities_in_bounds(w, bounds, pad=8):
        for b in getattr(city, "building_entities", {}).values():
            if b.kind == "farms" and not b.abandoned and len(farms) < 220:
                rec = _building_record(engine, city, b)
                farms.append({"x": rec["x"], "y": rec["y"],
                              "condition": rec["condition"],
                              "famine": city.famine > 0})
            elif b.kind == "mines" and not b.abandoned and len(mines) < 80:
                rec = _building_record(engine, city, b)
                mines.append({"x": rec["x"], "y": rec["y"],
                              "condition": rec["condition"],
                              "metal": round(city.stocks.get("metal", 0.0), 2)})
    return {"forests": forests, "farms": farms, "mines": mines, "snow": snow}


def _roads(w, bounds) -> list[list[float]]:
    cities = [c for c in w.cities.values() if c.alive]
    out: list[list[float]] = []
    seen = set()
    for city in cities:
        peers = [c for c in cities if c.id != city.id and c.civ_id == city.civ_id]
        peers = sorted(peers, key=lambda c:
                       abs(c.pos[0] - city.pos[0]) + abs(c.pos[1] - city.pos[1]))[:3]
        for other in peers:
            key = tuple(sorted((city.id, other.id)))
            if key in seen:
                continue
            seen.add(key)
            if not _line_intersects(bounds, city.pos, other.pos):
                continue
            trade_pull = (getattr(city, "trade_dependency", 0.0)
                          + getattr(other, "trade_dependency", 0.0)) * 0.16
            readiness = (getattr(city, "civic_stability", 1.0)
                         + getattr(other, "civic_stability", 1.0)) * 0.08
            crisis_drag = (getattr(city, "famine_risk", 0.0) + getattr(other, "famine_risk", 0.0)
                           + city.unrest + other.unrest) * 0.06
            weight = min(1.0, max(0.08, (city.wealth + other.wealth) / 160
                                  + trade_pull + readiness - crisis_drag))
            route_kind = _route_visual_kind(city, other)
            pts = _terrain_route(w, city.pos, other.pos)
            for a, b in zip(pts, pts[1:]):
                if _line_intersects(bounds, a, b):
                    out.append([a[1] / w.width, a[0] / w.height,
                                b[1] / w.width, b[0] / w.height,
                                city.civ_id, weight, route_kind])
                    if len(out) >= 720:
                        return out
    return out


def _route_visual_kind(a, b) -> str:
    trade = (a.wealth + b.wealth) / 140 + (getattr(a, "trade_dependency", 0.0)
                                           + getattr(b, "trade_dependency", 0.0)) * 0.25
    migration = getattr(a, "migration_pressure", 0.0) + getattr(b, "migration_pressure", 0.0)
    military = (getattr(a, "war_readiness", 0.0) + getattr(b, "war_readiness", 0.0)) * 0.35 \
        + a.unrest * 0.25 + b.unrest * 0.25
    if military > max(trade, migration) and military > 0.42:
        return "military"
    if migration > trade and migration > 0.32:
        return "migration"
    if trade > 0.35:
        return "trade"
    return "local"


def _terrain_route(w, a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """Render-only road polyline: favors land, lower slopes, and valley floors."""
    ay, ax = a
    by, bx = b
    dist = abs(ax - bx) + abs(ay - by)
    steps = max(4, min(18, int(dist / 7)))
    pts: list[tuple[int, int]] = []
    last = (int(ay), int(ax))
    for i in range(steps + 1):
        t = i / steps
        cy = int(round(ay + (by - ay) * t))
        cx = int(round(ax + (bx - ax) * t))
        best = _route_candidate(w, cy, cx, last, t, a, b)
        if not pts or best != pts[-1]:
            pts.append(best)
            last = best
    return pts


def _route_candidate(w, cy: int, cx: int, last: tuple[int, int], t: float,
                     a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    best = (max(0, min(w.height - 1, cy)), max(0, min(w.width - 1, cx)))
    best_score = float("inf")
    radius = 3
    for yy in range(max(0, cy - radius), min(w.height, cy + radius + 1)):
        for xx in range(max(0, cx - radius), min(w.width, cx + radius + 1)):
            line_d = abs((yy - cy)) + abs((xx - cx))
            progress_d = abs(yy - last[0]) + abs(xx - last[1])
            elev = float(w.elevation[yy, xx])
            water = float(w.water[yy, xx])
            land_penalty = 2.5 if not bool(w.land_mask[yy, xx]) else 0.0
            slope = _slope_at(w, yy, xx)
            valley = max(0.0, elev - min(float(w.elevation[max(0, yy - 1), xx]),
                                         float(w.elevation[min(w.height - 1, yy + 1), xx]),
                                         float(w.elevation[yy, max(0, xx - 1)]),
                                         float(w.elevation[yy, min(w.width - 1, xx + 1)])))
            score = line_d * 0.35 + progress_d * 0.04 + elev * 0.25 + water * 1.6 \
                + slope * 2.4 + valley * 0.8 + land_penalty
            if score < best_score:
                best_score = score
                best = (yy, xx)
    return best


def _slope_at(w, y: int, x: int) -> float:
    here = float(w.elevation[y, x])
    vals = [
        float(w.elevation[max(0, y - 1), x]),
        float(w.elevation[min(w.height - 1, y + 1), x]),
        float(w.elevation[y, max(0, x - 1)]),
        float(w.elevation[y, min(w.width - 1, x + 1)]),
    ]
    return max(abs(here - v) for v in vals)


def _districts(engine, bounds) -> list[dict[str, Any]]:
    out = []
    for city in _cities_in_bounds(engine.world, bounds, pad=12):
        counts: dict[str, int] = defaultdict(int)
        for b in getattr(city, "building_entities", {}).values():
            if not b.abandoned:
                counts[b.district] += 1
        if not counts:
            continue
        for district, count in counts.items():
            ox, oy = _district_offset(city.id, district, city.influence_radius)
            religion, rshare = engine.society.religion_of_city(city.id)
            culture, cshare = engine.society.culture_of_city(city.id)
            prosperity = _district_prosperity(city, district)
            damage = _district_damage(city, district)
            density = min(1.0, count / max(8, city.population / 650))
            material = _district_material(city, district, culture)
            out.append({
                "id": f"city:{city.id}:district:{district}",
                "city_id": city.id,
                "name": district,
                "x": (city.pos[1] + ox) / engine.world.width,
                "y": (city.pos[0] + oy) / engine.world.height,
                "radius": max(0.008, min(0.08, (count ** 0.5) * 0.004)),
                "boundary": _district_boundary(engine.world, city, district),
                "buildings": count,
                "density": round(density, 3),
                "archetypes": _district_archetypes(district),
                "material": material,
                "palette": _district_palette(district, prosperity, damage),
                "prosperity": round(prosperity, 3),
                "damage": round(damage, 3),
                "activity": _district_activity(city, district, count),
                "identity": _district_identity(city, district, count),
                "wealth": round(min(1.0, city.wealth / 90), 3),
                "condition": round(max(0.05, 1 - city.damage - city.unrest * 0.4), 3),
                "religion_id": religion.id if religion else None,
                "religion_share": round(rshare, 3),
                "culture_id": culture.id if culture else None,
                "culture_share": round(cshare, 3),
            })
    return out


def _buildings(engine, bounds, lod: int) -> list[dict[str, Any]]:
    out = []
    max_per_city = 220 if lod <= 1 else 90 if lod == 2 else 35
    for city in _cities_in_bounds(engine.world, bounds, pad=8):
        items = [b for b in getattr(city, "building_entities", {}).values()
                 if not b.abandoned or lod <= 2]
        items.sort(key=lambda b: _building_importance(b.kind), reverse=True)
        for b in items[:max_per_city]:
            out.append(_building_record(engine, city, b, high_detail=lod <= 2))
    return out


def _layout_offset(city, b) -> tuple[float, float, bool]:
    """City-local (x,y) offset for a building from the collision-free layout pass.
    Falls back to the legacy spiral offset for ids not in the layout. Third value is
    the overlap-debug flag (True only when the city was too crowded to place it)."""
    layout = _placement.layout_city(city, _building_footprint, _district_offset)
    slot = layout.get(b.id)
    if slot is not None:
        return slot["x"], slot["y"], bool(slot.get("skip"))
    return (*_building_offset(city.id, b.id, b.district, city.influence_radius), False)


def _building_record(engine, city, b, high_detail: bool = False) -> dict[str, Any]:
    ox, oy, overlap = _layout_offset(city, b)
    residents = _building_residents(engine, b.id)
    owner = engine.population.get(b.owner_id) if b.owner_id else None
    pressure = _policy_pressure_for_city(engine, city)
    archetype = _building_archetype(b.kind, b.district, b.wealth, b.condition)
    landmark = _city_landmark(engine, city)
    is_landmark = bool(landmark and landmark["building_id"] == b.id)
    rec = {
        "id": b.id, "kind": b.kind, "name": _building_name(city, b),
        "archetype": archetype, "city_id": city.id, "city": city.name,
        "district": b.district,
        "x": (city.pos[1] + ox) / engine.world.width,
        "y": (city.pos[0] + oy) / engine.world.height,
        "wealth": round(b.wealth, 3),
        "condition": round(b.condition, 3),
        "age": b.age,
        "abandoned": b.abandoned,
        "material": _building_material(city, b),
        "visual": {
            "height": round(_building_height(city, b), 3),
            "burned": city.damage > 0.35 and b.condition < 0.75,
            "cracked": b.condition < 0.65,
            "banner": b.kind in ("temples", "barracks", "market", "noble_district"),
            "chimney": b.kind in ("workshops", "archives") and city.infrastructure > 4,
            "rubble": b.abandoned or b.condition < 0.28,
            "footprint": round(_building_footprint(b.kind, b.district, b.wealth), 3),
            "landmark": is_landmark,
            "landmark_reason": landmark["reason"] if is_landmark else "",
            "skyline_score": round(_city_skyline_score(city), 3),
            "resource_signal": _building_resource_signal(city, b),
            "light": _building_light_signal(city, b),
            "overlap_debug": overlap,
        },
        "workers": len(b.workers),
        "worker_ids": b.workers[:16],
        "owner_id": b.owner_id,
        "owner": owner.name if owner else None,
        "residents": len(residents),
        "resident_ids": residents[:16],
        "activity": _building_activity(engine, city, b, residents),
        "influence": {
            "religion": engine.society.religion_of_city(city.id)[1],
            "faction": _faction_pressure(engine, city.id),
            "rebellion": pressure["rebellion"],
        },
        "religion_id": engine.society.religion_of_city(city.id)[0].id
        if engine.society.religion_of_city(city.id)[0] else None,
    }
    if high_detail:
        rec["inventory"] = b.inventory
        rec["production"] = b.production
        rec["history"] = b.history[-5:]
    return rec


def _skylines(engine, bounds) -> list[dict[str, Any]]:
    out = []
    for city in _cities_in_bounds(engine.world, bounds, pad=10):
        buildings = getattr(city, "buildings", {})
        landmark = _city_landmark(engine, city)
        trade = min(1.0, (buildings.get("market", 0) + buildings.get("docks", 0) * 2) / 8
                    + city.wealth / 140)
        religion = engine.society.religion_of_city(city.id)[1]
        knowledge = min(1.0, buildings.get("archives", 0) / 4
                        + city.stocks.get("knowledge", 0.0) / 120
                        + getattr(city, "education", 0.0) * 0.25)
        war = min(1.0, buildings.get("barracks", 0) / 4 + city.damage + city.unrest * 0.5)
        civ = engine.world.civilizations.get(city.civ_id)
        out.append({
            "city_id": city.id, "name": city.name,
            "x": city.pos[1] / engine.world.width,
            "y": city.pos[0] / engine.world.height,
            # nation identity so the renderer can tint cities/territory by civilization
            "civ_id": city.civ_id,
            "civ_color": getattr(civ, "color", "#9b8cff") if civ else "#9b8cff",
            "is_capital": bool(civ and getattr(civ, "capital_city_id", None) == city.id),
            "population": int(city.population),
            "wealth": round(min(1.0, city.wealth / 100), 3),
            "trade": round(trade, 3),
            "religion": round(religion, 3),
            "knowledge": round(knowledge, 3),
            "war": round(war, 3),
            "industry": round(_city_industry(city), 3),
            "famine_risk": round(getattr(city, "famine_risk", 0.0), 3),
            "demand_pressure": round(getattr(city, "demand_pressure", 0.0), 3),
            "trade_dependency": round(getattr(city, "trade_dependency", 0.0), 3),
            "war_readiness": round(getattr(city, "war_readiness", 0.0), 3),
            "civic_stability": round(getattr(city, "civic_stability", 1.0), 3),
            "education": round(getattr(city, "education", 0.0), 3),
            "urbanization": round(getattr(city, "urbanization", 0.0), 3),
            "migration_pressure": round(getattr(city, "migration_pressure", 0.0), 3),
            "heritage": round(getattr(city, "heritage", 0.0), 3),
            "trauma": round(getattr(city, "trauma", 0.0), 3),
            "economic_health": round(getattr(city, "economic_health", 1.0), 3),
            "unrest": round(city.unrest, 3),
            "plague": city.plague > 0,
            "famine": city.famine > 0,
            "damage": round(city.damage, 3),
            "density": round(min(1.0, sum(buildings.values()) / 220), 3),
            "height": round(_city_skyline_score(city), 3),
            "lights": _city_light_profile(engine, city, trade, religion, knowledge, war),
            "visuals": _city_visual_signals(city),
            "landmark": landmark,
        })
    return out


def _city_industry(city) -> float:
    b = getattr(city, "buildings", {})
    stocks = getattr(city, "stocks", {})
    return max(0.0, min(1.0,
        b.get("workshops", 0) / 9
        + b.get("mines", 0) / 8
        + stocks.get("metal", 0.0) / 180
        + stocks.get("energy", 0.0) / 120))


def _city_visual_signals(city) -> dict[str, float | bool]:
    shortages = getattr(city, "shortages", {})
    surplus = getattr(city, "surplus", {})
    classes = getattr(city, "class_mix", {})
    professions = getattr(city, "professions", {})
    return {
        "food_shortage": round(shortages.get("food", 0.0), 3),
        "metal_shortage": round(shortages.get("metal", 0.0), 3),
        "labor_shortage": round(shortages.get("labor", 0.0), 3),
        "knowledge_surplus": round(surplus.get("knowledge", 0.0), 3),
        "luxury_surplus": round(surplus.get("luxury", 0.0), 3),
        "trade_dependency": round(getattr(city, "trade_dependency", 0.0), 3),
        "civic_stability": round(getattr(city, "civic_stability", 1.0), 3),
        "education": round(getattr(city, "education", 0.0), 3),
        "urbanization": round(getattr(city, "urbanization", 0.0), 3),
        "migration_pressure": round(getattr(city, "migration_pressure", 0.0), 3),
        "heritage": round(getattr(city, "heritage", 0.0), 3),
        "trauma": round(getattr(city, "trauma", 0.0), 3),
        "elite_share": round(classes.get("elite", 0.0), 3),
        "poor_share": round(classes.get("poor", 0.0), 3),
        "farmers": round(professions.get("farmers", 0.0), 3),
        "traders": round(professions.get("traders", 0.0), 3),
        "scholars": round(professions.get("scholars", 0.0), 3),
        "soldiers": round(professions.get("soldiers", 0.0), 3),
        "old_core": city.founded_tick < 0 or getattr(city, "age", 0) > 0,
        "abandoned": bool(getattr(city, "abandoned_tick", None) is not None),
    }


def _city_light_profile(engine, city, trade: float, religion: float, knowledge: float,
                        war: float) -> dict[str, Any]:
    """Night-light facts for the renderer. Brightness is real prosperity:
    wealth × population × infrastructure × economic health, modulated by crises."""
    pop = max(0.0, min(1.0, city.population / 32000))
    wealth = max(0.0, min(1.0, city.wealth / 100))
    infra = max(0.0, min(1.0, city.infrastructure / 10))
    econ = max(0.0, min(1.0, getattr(city, "economic_health", 1.0)))
    crisis = max(getattr(city, "famine_risk", 0.0), city.unrest, city.damage)
    intensity = max(0.0, min(1.0, (0.2 + pop * 0.36 + wealth * 0.26 + infra * 0.24)
                             * (0.45 + econ * 0.55) * (1.0 - crisis * 0.42)))
    count = int(max(0, min(90, 4 + city.population / 900 + city.infrastructure * 2
                          + city.wealth / 5)))
    color = "#ffc86a"
    if knowledge > max(trade, religion, war) and knowledge > 0.34:
        color = "#b7d9ff"
    elif religion > max(trade, knowledge, war) and religion > 0.42:
        color = "#ffe9a8"
    elif war > 0.5 or city.unrest > 0.55:
        color = "#ff7a58"
    elif getattr(city, "famine_risk", 0.0) > 0.45 or city.plague > 0:
        color = "#b694ff" if city.plague > 0 else "#d9a34b"
    culture, cshare = engine.society.culture_of_city(city.id)
    hue_seed = culture.id if culture else city.civ_id
    return {
        "count": count,
        "intensity": round(intensity, 3),
        "color": color,
        "culture_seed": int(hue_seed or 0),
        "trade": round(trade, 3),
        "knowledge": round(knowledge, 3),
        "religion": round(religion, 3),
        "crisis": round(crisis, 3),
    }


def _building_resource_signal(city, b) -> str:
    shortages = getattr(city, "shortages", {})
    surplus = getattr(city, "surplus", {})
    if b.kind == "farms" and shortages.get("food", 0.0) > 0.35:
        return "food_shortage"
    if b.kind in ("workshops", "mines") and (surplus.get("metal", 0.0) > 0.15
                                             or surplus.get("energy", 0.0) > 0.15):
        return "industry"
    if b.kind in ("archives",) and surplus.get("knowledge", 0.0) > 0.15:
        return "knowledge"
    if b.kind in ("market", "docks") and getattr(city, "trade_dependency", 0.0) > 0.32:
        return "trade_dependency"
    if city.unrest > 0.55:
        return "unrest"
    if city.plague > 0:
        return "plague"
    return ""


def _building_light_signal(city, b) -> dict[str, Any]:
    base = max(0.0, min(1.0, getattr(city, "economic_health", 1.0) * 0.45
                        + min(1.0, city.wealth / 90) * 0.35
                        + min(1.0, city.infrastructure / 10) * 0.2))
    if b.kind in ("archives",):
        return {"kind": "knowledge", "intensity": round(base * 0.9, 3), "color": "#b7d9ff"}
    if b.kind in ("temples",):
        return {"kind": "religion", "intensity": round(base * 0.8, 3), "color": "#ffe9a8"}
    if b.kind in ("market", "docks"):
        return {"kind": "trade", "intensity": round(base, 3), "color": "#ffc86a"}
    if b.kind in ("barracks",) or city.unrest > 0.6:
        return {"kind": "war", "intensity": round(base * 0.75, 3), "color": "#ff7a58"}
    return {"kind": "home", "intensity": round(base * 0.45, 3), "color": "#f0b75c"}


def _citizens(engine, bounds, lod: int) -> dict[str, Any]:
    crowds = []
    agents = []
    for city in _cities_in_bounds(engine.world, bounds, pad=8):
        action_counts = Counter(p.last_action or "idle"
                                for p in engine.population.residents(city.id))
        if city.population > 0:
            crowds.append({
                "city_id": city.id,
                "x": city.pos[1] / engine.world.width,
                "y": city.pos[0] / engine.world.height,
                "population": int(city.population),
                "materialized": len(engine.population.residents(city.id)),
                "routine": dict(action_counts.most_common(8)),
                "stress": round(city.unrest, 3),
                "health": round(max(0.08, min(1.0, getattr(city, "civic_stability", 1.0)
                                               * (1.0 - getattr(city, "famine_risk", 0.0) * 0.45))), 3),
                "famine_risk": round(getattr(city, "famine_risk", 0.0), 3),
                "migration_pressure": _policy_pressure_for_city(engine, city)["migration"],
            })
        if lod > 2:
            continue
        for p in engine.population.residents(city.id)[:160]:
            bx, by = _citizen_position(engine, city, p)
            agents.append({
                "id": p.id, "entity_id": f"person:{p.id}",
                "name": p.name, "city_id": city.id,
                "x": bx, "y": by,
                "home_building": p.home_building,
                "work_building": p.work_building,
                "path": _citizen_path(engine, city, p),
                "routine": p.last_action or "idle",
                "group": _citizen_group(p),
                "goal": p.dominant_goal(),
                "home": _entity_xy(engine, p.home_building),
                "work": _entity_xy(engine, p.work_building),
                "recent_memory": p.memory.top(1)[0].text if p.memory.top(1) else "",
                "trust_observer": round(p.trust_observer, 3),
                "mood": round(p.mood, 3),
                "stress": round(p.stress, 3),
                "wealth": round(min(1.0, p.wealth / 30), 3),
                "religion_id": p.religion_id,
                "factions": p.faction_ids[:4],
            })
    return {"crowds": crowds, "agents": agents}


def _units(w, bounds, lod: int) -> list[dict[str, Any]]:
    if lod > 3:
        return []
    out = []
    x0, y0, x1, y1 = bounds
    for u in w.units.values():
        y, x = u.pos
        if x0 - 4 <= x <= x1 + 4 and y0 - 4 <= y <= y1 + 4:
            out.append({"id": u.id, "kind": u.kind, "civ_id": u.civ_id,
                        "x": x / w.width, "y": y / w.height,
                        "target": [u.target[1] / w.width, u.target[0] / w.height],
                        "payload": round(u.payload, 2), "cargo": u.cargo})
    return out[:400]


def _scars(engine, bounds) -> list[dict[str, Any]]:
    w = engine.world
    out = []
    for m in w.markers:
        if _point_in_bounds(bounds, m["y"], m["x"], pad=4):
            out.append({"kind": _marker_scar_kind(m["kind"]), "marker_kind": m["kind"],
                        "x": m["x"] / w.width,
                        "y": m["y"] / w.height, "label": m.get("label", ""),
                        "ttl": m.get("ttl", 0)})
    for site in getattr(w, "historical_sites", [])[-600:]:
        sy = float(site.get("y", -9999))
        sx = float(site.get("x", -9999))
        if _point_in_bounds(bounds, sy, sx, pad=8):
            out.append({"kind": site.get("kind", "scar"),
                        "event_type": site.get("event_type"),
                        "x": sx / w.width, "y": sy / w.height,
                        "title": site.get("title", ""),
                        "tick": site.get("tick"),
                        "age": max(0, w.tick - int(site.get("tick", w.tick))),
                        "city_id": site.get("city_id"),
                        "intensity": round(float(site.get("intensity", 0.35)), 3),
                        "persistent": True})
    for ev in engine.history.recent(350):
        cid = ev.get("city_id")
        city = w.cities.get(cid) if cid is not None else None
        if city and _point_in_bounds(bounds, city.pos[0], city.pos[1], pad=6):
            kind = _event_scar_kind(ev.get("type", ""))
            out.append({"kind": kind, "event_type": ev.get("type"),
                        "x": city.pos[1] / w.width, "y": city.pos[0] / w.height,
                        "title": ev.get("title", ""), "tick": ev.get("tick")})
    return out[-120:]


def _overlays(engine, bounds) -> dict[str, list[dict[str, Any]]]:
    values = []
    for city in _cities_in_bounds(engine.world, bounds, pad=12):
        pressure = _policy_pressure_for_city(engine, city)
        shortages = getattr(city, "shortages", {})
        civ = engine.world.civilizations.get(city.civ_id)
        values.append({"city_id": city.id, "x": city.pos[1] / engine.world.width,
                       "y": city.pos[0] / engine.world.height,
                       # political map: a city tinted by the nation that holds it
                       "civ_id": city.civ_id,
                       "civ_color": getattr(civ, "color", "#9b8cff") if civ else "#9b8cff",
                       "political": round(min(1.0, 0.45 + city.population / 24000), 3),
                       "economy": round(1.0 - city.economic_health, 3),
                       "population": round(min(1.0, city.population / 30000), 3),
                       "religion": engine.society.religion_of_city(city.id)[1],
                       "faction": _faction_pressure(engine, city.id),
                       "migration": pressure["migration"],
                       "war": pressure["aggression"],
                       "rebellion_probability": pressure["rebellion"],
                       "policy_confidence": engine.world.species_brain.status().get("confidence", 0.0),
                       "resources": round(max(0.0, min(1.0, 1.0 - getattr(city, "demand_pressure", 0.0)
                                                      + getattr(city, "trade_dependency", 0.0) * 0.18)), 3),
                       "food_shortage": round(shortages.get("food", 0.0), 3),
                       "metal_shortage": round(shortages.get("metal", 0.0), 3),
                       "labor_shortage": round(shortages.get("labor", 0.0), 3),
                       "climate": _climate_pressure(engine.world, city)})
    return {"cities": values}


def _policy_pressure_for_city(engine, city) -> dict[str, float]:
    status = engine.world.species_brain.status()
    delta = status.get("behavior_delta", {})
    demand = max(1e-6, city.population * 0.0013)
    scarcity = max(0.0, min(1.0, 1.0 - city.food_production / demand))
    unrest = max(0.0, min(1.0, city.unrest))
    return {
        "migration": round(max(0.0, min(1.0, scarcity * 0.42 + unrest * 0.3
                                        + getattr(city, "trade_dependency", 0.0) * 0.16
                                        + getattr(city, "demand_pressure", 0.0) * 0.18
                                        + max(0.0, delta.get("migrate", 0)) * 2)), 3),
        "aggression": round(max(0.0, min(1.0, unrest * 0.45
                                         + (1.0 - getattr(city, "war_readiness", 0.0)) * 0.12
                                         + max(0.0, delta.get("feud", 0)) * 2)), 3),
        "cooperation": round(max(0.0, min(1.0, city.economic_health * 0.45
                                          + getattr(city, "civic_stability", 1.0) * 0.18
                                          + max(0.0, delta.get("socialize", 0)) * 2)), 3),
        "religious_openness": round(max(0.0, min(1.0, city.culture / 140
                                                + max(0.0, delta.get("worship", 0)) * 2)), 3),
        "faction": round(_faction_pressure(engine, city.id), 3),
        "rebellion": round(max(0.0, min(1.0, unrest * 0.65 + scarcity * 0.25
                                       + max(0.0, delta.get("feud", 0)) * 1.4)), 3),
    }


def _counterfactual_explanation(remove: str) -> str:
    if remove == "food_scarcity":
        return "Food pressure is treated as absent, reducing migration and revolt pressure."
    if remove == "religion":
        return "Dominant religion is treated as absent, increasing religious openness and faction competition."
    if remove == "unrest":
        return "Civic unrest is treated as resolved, reducing rebellion and aggression pressure."
    return "Baseline policy pressure from current simulation state."


def _district_prosperity(city, district: str) -> float:
    base = min(1.0, city.wealth / 90) * 0.5 + city.economic_health * 0.5
    if district == "poor":
        base *= 0.45
    elif district in ("civic", "sacred", "market", "waterfront", "noble"):
        base = min(1.0, base + 0.18)
    elif district == "farmland" and city.famine > 0:
        base *= 0.55
    return max(0.0, min(1.0, base))


def _district_damage(city, district: str) -> float:
    dmg = city.damage + city.unrest * 0.25
    if district == "poor":
        dmg += city.unrest * 0.2
    if district == "farmland" and city.famine > 0:
        dmg += 0.22
    return max(0.0, min(1.0, dmg))


def _district_material(city, district: str, culture) -> str:
    arch = getattr(culture, "architecture", "") if culture else ""
    if district in ("industrial", "mines"):
        return "stone-metal"
    if district == "waterfront":
        return "wood-plank"
    if district == "farmland":
        return "earth-thatch"
    if district == "sacred":
        return "pale-stone" if city.wealth > 35 else "painted-wood"
    if "brick" in arch.lower() or city.infrastructure > 5:
        return "brick"
    return "wood-stone"


def _district_palette(district: str, prosperity: float, damage: float) -> dict[str, str]:
    base = {
        "poor": ("#735b48", "#9a6851"),
        "residential": ("#a58d68", "#d0b98b"),
        "market": ("#b8823e", "#e1bd68"),
        "sacred": ("#c7c4aa", "#f0e5bd"),
        "scholarly": ("#6f86a8", "#9db4d8"),
        "industrial": ("#6f7378", "#a0a4a8"),
        "farmland": ("#789f52", "#b4d27a"),
        "waterfront": ("#597f96", "#86bed4"),
        "military": ("#8d5855", "#c77d72"),
        "noble": ("#9f7f3e", "#d4b26a"),
        "civic": ("#806b62", "#bd9b85"),
    }.get(district, ("#8f826e", "#c0ae8a"))
    return {"base": base[0], "accent": base[1],
            "prosperity": round(prosperity, 3), "damage": round(damage, 3)}


def _district_archetypes(district: str) -> list[str]:
    return {
        "poor": ["slum_shack", "hut", "ruin"],
        "residential": ["small_house", "dense_house", "townhouse"],
        "market": ["market_stall", "warehouse", "tavern"],
        "sacred": ["temple", "shrine", "memorial"],
        "scholarly": ["academy", "hospital", "townhouse"],
        "industrial": ["workshop", "warehouse", "chimney"],
        "farmland": ["farm_plot", "barn", "hut"],
        "waterfront": ["dock", "warehouse", "fishing_hut"],
        "military": ["barracks", "tower", "wall_segment"],
        "noble": ["manor", "townhouse", "memorial"],
        "civic": ["academy", "tower", "townhouse"],
    }.get(district, ["small_house"])


def _district_activity(city, district: str, count: int) -> dict[str, float]:
    return {
        "population": round(min(1.0, city.population / 25000), 3),
        "density": round(min(1.0, count / 80), 3),
        "trade": round(min(1.0, city.wealth / 90), 3) if district in ("market", "waterfront") else 0.0,
        "worship": round(min(1.0, city.culture / 100), 3) if district == "sacred" else 0.0,
        "military": round(min(1.0, city.infrastructure / 10), 3) if district == "military" else 0.0,
        "famine": 1.0 if city.famine > 0 and district == "farmland" else 0.0,
    }


def _district_identity(city, district: str, count: int) -> dict[str, Any]:
    classes = getattr(city, "class_mix", {})
    professions = getattr(city, "professions", {})
    shortages = getattr(city, "shortages", {})
    score = {
        "poverty": classes.get("poor", 0.0) + city.unrest * 0.35
                   + shortages.get("food", 0.0) * 0.25,
        "wealth": classes.get("elite", 0.0) + min(1.0, city.wealth / 90) * 0.45,
        "trade": professions.get("traders", 0.0) + getattr(city, "trade_dependency", 0.0) * 0.35,
        "knowledge": professions.get("scholars", 0.0) + getattr(city, "education", 0.0) * 0.45,
        "military": professions.get("soldiers", 0.0) + getattr(city, "war_readiness", 0.0) * 0.35,
        "faith": professions.get("priests", 0.0) + city.culture / 180,
        "industry": professions.get("craftspeople", 0.0) + professions.get("miners", 0.0),
        "agriculture": professions.get("farmers", 0.0) + (0.4 if district == "farmland" else 0.0),
        "memory": getattr(city, "heritage", 0.0),
        "trauma": getattr(city, "trauma", 0.0) + city.damage * 0.35,
    }
    district_bias = {
        "poor": "poverty", "noble": "wealth", "market": "trade",
        "waterfront": "trade", "scholarly": "knowledge", "military": "military",
        "sacred": "faith", "industrial": "industry", "farmland": "agriculture",
    }.get(district)
    if district_bias:
        score[district_bias] = score.get(district_bias, 0.0) + 0.35
    dominant = max(score, key=score.get)
    return {
        "dominant": dominant,
        "scores": {k: round(max(0.0, min(1.0, v)), 3) for k, v in score.items()},
        "density": round(min(1.0, count / max(5.0, city.population / 720.0)), 3),
        "old": round(min(1.0, max(0, city.age if hasattr(city, "age") else 0) / 1800), 3),
    }


def _district_boundary(w, city, district: str) -> list[list[float]]:
    ox, oy = _district_offset(city.id, district, city.influence_radius)
    radius = max(1.0, city.influence_radius * 0.22)
    pts = []
    jitter = _stable_float(f"{city.id}:{district}:j") * 0.22
    for i in range(10):
        a = i / 10 * math.tau
        r = radius * (0.78 + 0.22 * math.sin(a * 3 + jitter))
        pts.append([(city.pos[1] + ox + math.cos(a) * r) / w.width,
                    (city.pos[0] + oy + math.sin(a) * r) / w.height])
    return pts


def _building_archetype(kind: str, district: str, wealth: float, condition: float) -> str:
    if condition < 0.25:
        return "ruin"
    if kind == "homes":
        return "townhouse" if wealth > 0.75 else "dense_house" if wealth > 0.35 else "small_house"
    if kind == "slums":
        return "slum_shack"
    if kind == "farms":
        return "farm_plot"
    if kind == "market":
        return "market_stall" if wealth < 0.55 else "warehouse"
    if kind == "docks":
        return "dock"
    if kind == "temples":
        return "temple" if wealth > 0.4 else "shrine"
    if kind == "archives":
        return "academy"
    if kind == "barracks":
        return "barracks"
    if kind == "mines":
        return "mine_entrance"
    if kind == "tavern":
        return "tavern"
    if kind == "noble_district":
        return "palace" if wealth > 0.75 else "manor"
    if kind == "workshops":
        return "workshop"
    return "hut" if district == "poor" else "small_house"


def _building_material(city, b) -> str:
    if b.kind in ("farms", "slums"):
        return "thatch"
    if b.kind in ("temples", "archives", "barracks", "mines", "noble_district"):
        return "stone"
    if b.kind in ("docks", "market", "tavern"):
        return "timber"
    if city.infrastructure > 5:
        return "brick"
    return "wood"


def _building_height(city, b) -> float:
    city_height = min(1.5, city.wealth / 90 * 0.7 + city.infrastructure / 10 * 0.5
                      + city.population / 60000 * 0.3)
    if b.kind == "noble_district":
        return 1.8 + b.wealth * 1.7 + city_height
    if b.kind in ("temples", "archives", "barracks"):
        return 1.3 + b.wealth + city_height * 0.65
    if b.kind == "homes":
        return 0.7 + b.wealth * 0.9 + city_height * 0.35
    if b.kind == "slums":
        return 0.35
    return 0.75 + b.wealth * 0.55 + city_height * 0.2


def _city_skyline_score(city) -> float:
    return min(1.0, city.population / 35000 * 0.35 + city.wealth / 100 * 0.35
               + city.infrastructure / 10 * 0.2 + city.culture / 120 * 0.1)


def _city_landmark(engine, city) -> dict[str, Any] | None:
    buildings = getattr(city, "building_entities", {})
    live = [b for b in buildings.values() if not b.abandoned]
    if not live:
        return None
    rel_share = engine.society.religion_of_city(city.id)[1]
    scores = [
        ("wealth", city.wealth / 90, ("noble_district",), "palace"),
        ("trade", city.wealth / 80 + city.buildings.get("docks", 0) * 0.25,
         ("market", "docks"), "market"),
        ("religion", rel_share + city.buildings.get("temples", 0) * 0.18,
         ("temples",), "temple"),
        ("knowledge", city.stocks.get("knowledge", 0.0) / 90
         + city.buildings.get("archives", 0) * 0.25, ("archives",), "academy"),
        ("war", city.damage + city.unrest * 0.55 + city.buildings.get("barracks", 0) * 0.2,
         ("barracks",), "fortress"),
    ]
    reason, score, kinds, archetype = max(scores, key=lambda s: s[1])
    if score < 0.55:
        return None
    candidates = [b for b in live if b.kind in kinds]
    if not candidates:
        return None
    b = max(candidates, key=lambda x: (x.wealth, x.condition, -x.age))
    ox, oy, _ = _layout_offset(city, b)
    return {
        "building_id": b.id,
        "reason": reason,
        "archetype": archetype,
        "score": round(min(1.0, float(score)), 3),
        "x": round((city.pos[1] + ox) / engine.world.width, 5),
        "y": round((city.pos[0] + oy) / engine.world.height, 5),
    }


def _building_name(city, b) -> str:
    label = b.kind.replace("_", " ").title()
    suffix = b.id.rsplit(":", 1)[-1]
    return f"{city.name} {label} {suffix}"


def _building_residents(engine, building_id: str) -> list[int]:
    return [p.id for p in engine.population.people.values()
            if p.alive and p.home_building == building_id][:80]


def _building_activity(engine, city, b, residents: list[int]) -> dict[str, Any]:
    workers = [engine.population.get(pid) for pid in b.workers[:20]]
    actions = Counter(p.last_action or "idle" for p in workers if p)
    if b.kind == "temples":
        activity = "worship" if city.culture > 20 else "quiet rites"
    elif b.kind == "market":
        activity = "trade" if city.wealth > 10 else "barter"
    elif b.kind == "barracks":
        activity = "drilling"
    elif b.kind == "farms":
        activity = "harvest" if city.famine == 0 else "famine recovery"
    elif b.abandoned:
        activity = "abandoned"
    else:
        activity = actions.most_common(1)[0][0] if actions else "idle"
    return {"current": activity, "routines": dict(actions), "residents": len(residents)}


def _citizen_group(p) -> str:
    if p.age < 14:
        return "children"
    if p.social_class in ("noble", "gentry"):
        return "nobles"
    if p.social_class == "destitute":
        return "poor"
    if p.last_action == "worship" or p.profession == "priest":
        return "worshippers"
    if p.profession == "soldier":
        return "soldiers"
    if p.last_action == "migrate":
        return "migrants"
    if p.profession in ("trader", "merchant"):
        return "merchants"
    return "workers"


def _entity_xy(engine, building_id: str) -> list[float] | None:
    ent = entity_payload(engine, f"building:{building_id}") if building_id else None
    if ent and ent.get("data"):
        return [ent["data"]["x"], ent["data"]["y"]]
    return None


def _citizen_path(engine, city, p) -> list[list[float]]:
    home = _entity_xy(engine, p.home_building)
    work = _entity_xy(engine, p.work_building)
    cur = _citizen_position(engine, city, p)
    if p.last_action == "migrate":
        return [[cur[0], cur[1]]]
    if p.last_action in ("work", "study", "worship") and home and work:
        return [home, work]
    if home:
        return [home, [cur[0], cur[1]]]
    return [[cur[0], cur[1]]]


def _marker_scar_kind(kind: str) -> str:
    return {
        "battle": "battlefield", "march": "old_road", "famine": "abandoned_farms",
        "plague": "plague_marker", "migration": "migration_camp",
        "founded": "monument",
    }.get(kind, kind)


def _event_scar_kind(event_type: str) -> str:
    return {
        "war": "battlefield", "collapse": "ruin", "famine": "abandoned_farms",
        "plague": "plague_marker", "religion_founded": "sacred_site",
        "observer": "sacred_site", "settlement": "monument",
        "rebellion": "rebellion_plaza", "rumor": "rebellion_plaza",
        "economy": "market_crash",
    }.get(event_type, "memorial")


def _flat(arr, ndigits: int) -> list[float]:
    return np.round(arr, ndigits).astype(float).flatten().tolist()


def _cities_in_bounds(w, bounds, pad=0):
    x0, y0, x1, y1 = bounds
    return [c for c in w.cities.values() if c.alive
            and x0 - pad <= c.pos[1] <= x1 + pad
            and y0 - pad <= c.pos[0] <= y1 + pad]


def _point_in_bounds(bounds, y, x, pad=0) -> bool:
    x0, y0, x1, y1 = bounds
    return x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad


def _line_intersects(bounds, a, b) -> bool:
    x0, y0, x1, y1 = bounds
    minx, maxx = sorted((a[1], b[1]))
    miny, maxy = sorted((a[0], b[0]))
    return not (maxx < x0 or minx > x1 or maxy < y0 or miny > y1)


def _district_offset(city_id: int, district: str, radius: float) -> tuple[float, float]:
    order = ["residential", "market", "sacred", "scholarly", "industrial",
             "farmland", "waterfront", "military", "noble", "poor"]
    idx = order.index(district) if district in order else _stable_int(district, 8)
    ang = idx / max(1, len(order)) * math.tau + _stable_float(f"{city_id}:{district}") * 0.35
    r = max(1.2, radius * (0.18 + 0.08 * (idx % 4)))
    return math.cos(ang) * r, math.sin(ang) * r


def _building_offset(city_id: int, bid: str, district: str, radius: float) -> tuple[float, float]:
    dx, dy = _district_offset(city_id, district, radius)
    idx = _building_sequence_index(bid)
    footprint = _building_footprint(_building_kind_from_id(bid), district)
    density = {
        "poor": 0.78, "residential": 0.92, "industrial": 1.0,
        "market": 1.08, "waterfront": 1.1, "sacred": 1.18,
        "military": 1.22, "scholarly": 1.26, "noble": 1.38,
        "farmland": 1.65,
    }.get(district, 1.0)
    anchor_kinds = {"temples", "market", "docks", "archives", "barracks", "noble_district"}
    if _building_kind_from_id(bid) in anchor_kinds and idx == 0:
        return dx, dy
    golden = math.pi * (3.0 - math.sqrt(5.0))
    phase = _stable_float(f"{city_id}:{district}:phase") * math.tau
    spacing = max(0.34, footprint * 1.72 * density)
    ring = math.sqrt(idx + 0.5) * spacing
    # Keep each district visually coherent instead of spraying buildings across the
    # full city radius. Farmland gets the widest envelope; poor districts pack tight.
    district_radius = max(0.85, radius * {
        "farmland": 0.36, "poor": 0.16, "residential": 0.19, "noble": 0.23,
        "market": 0.18, "waterfront": 0.2, "industrial": 0.22,
    }.get(district, 0.2))
    ring = min(ring, district_radius)
    a = phase + idx * golden
    jitter = (_stable_float(bid + ":j") - 0.5) * spacing * 0.18
    tangent = _stable_float(bid + ":t") - 0.5
    return (dx + math.cos(a) * (ring + jitter) - math.sin(a) * tangent * spacing * 0.16,
            dy + math.sin(a) * (ring + jitter) + math.cos(a) * tangent * spacing * 0.16)


def _building_sequence_index(bid: str) -> int:
    try:
        return max(0, int(bid.rsplit(":", 1)[-1]))
    except (TypeError, ValueError):
        return _stable_int(bid, 4096)


def _building_kind_from_id(bid: str) -> str:
    parts = str(bid).split(":")
    return parts[-2] if len(parts) >= 3 else ""


def _building_footprint(kind: str, district: str = "", wealth: float = 0.0) -> float:
    base = {
        "slums": 0.22, "homes": 0.28, "farms": 0.36, "tavern": 0.34,
        "workshops": 0.38, "market": 0.5, "docks": 0.52, "mines": 0.44,
        "temples": 0.58, "archives": 0.56, "barracks": 0.62,
        "noble_district": 0.72,
    }.get(kind, 0.34)
    if district == "poor":
        base *= 0.9
    elif district in ("noble", "sacred", "scholarly", "military"):
        base *= 1.12
    elif district == "farmland":
        base *= 1.18
    return base * (1.0 + max(0.0, min(1.0, wealth)) * 0.12)


def _citizen_position(engine, city, p) -> tuple[float, float]:
    target = p.work_building if p.last_action in ("work", "study", "worship") else p.home_building
    ent = entity_payload(engine, f"building:{target}")
    if ent and ent.get("data"):
        return ent["data"]["x"], ent["data"]["y"]
    ox, oy = _building_offset(city.id, f"person:{p.id}", "residential", city.influence_radius)
    return (city.pos[1] + ox) / engine.world.width, (city.pos[0] + oy) / engine.world.height


def _building_importance(kind: str) -> int:
    order = {"temples": 10, "market": 9, "docks": 9, "barracks": 8,
             "archives": 8, "noble_district": 8, "mines": 7,
             "workshops": 6, "tavern": 5, "farms": 4, "slums": 3, "homes": 2}
    return order.get(kind, 1)


def _faction_pressure(engine, city_id: int) -> float:
    pressure = 0.0
    for f in engine.society.factions.values():
        if f.alive and f.seat_city == city_id:
            pressure = max(pressure, f.influence)
    return round(pressure, 3)


def _climate_pressure(w, city) -> float:
    y, x = city.pos
    temp = float(w.temperature[y, x])
    rain = float(w.rainfall[y, x])
    return round(max(abs(temp - 18) / 35, max(0.0, 0.25 - rain)), 3)


def _stable_int(text: str, mod: int) -> int:
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16) % max(1, mod)


def _stable_float(text: str) -> float:
    return _stable_int(text, 10_000) / 10_000.0
