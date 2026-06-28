"""Simulation-side roads.

Roads used to be generated only by the renderer, so they looked useful but could not
influence settlement. This module keeps a compact terrain-aware road graph and a cached
road-access grid that city placement, growth, and citizen perception can read.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .world import WorldState


def rebuild(world: "WorldState") -> None:
    cities = [c for c in world.cities.values() if c.alive]
    roads: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for city in cities:
        peers = sorted(
            [c for c in cities if c.id != city.id],
            key=lambda c: abs(c.pos[0] - city.pos[0]) + abs(c.pos[1] - city.pos[1]),
        )
        same = [c for c in peers if c.civ_id == city.civ_id][:3]
        foreign = [c for c in peers if c.civ_id != city.civ_id][:2]
        for other in same + foreign:
            key = tuple(sorted((city.id, other.id)))
            if key in seen:
                continue
            seen.add(key)
            pts = terrain_route(world, city.pos, other.pos)
            if len(pts) < 2:
                continue
            kind = _road_kind(city, other)
            roads.append({
                "id": f"road:{key[0]}:{key[1]}",
                "points": pts,
                "city_ids": list(key),
                "civ_id": city.civ_id if city.civ_id == other.civ_id else None,
                "kind": kind,
                "importance": _importance(city, other, kind),
            })
            if len(roads) >= 220:
                break
    world.road_graph = roads
    world.road_access = _road_access(world, roads)
    try:
        from . import terrain
        terrain.compute_buildable_score(world)
    except Exception:
        pass


def step(world: "WorldState") -> None:
    sig = tuple(sorted((c.id, c.civ_id, c.pos)
                       for c in world.cities.values() if c.alive))
    if getattr(world, "_road_sig", None) == sig and world.tick % 80 != 0:
        return
    world._road_sig = sig
    rebuild(world)


def terrain_route(world: "WorldState", a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    ay, ax = a
    by, bx = b
    dist = abs(ax - bx) + abs(ay - by)
    steps = max(4, min(28, int(dist / 5)))
    pts: list[tuple[int, int]] = []
    last = (int(ay), int(ax))
    for i in range(steps + 1):
        t = i / steps
        cy = int(round(ay + (by - ay) * t))
        cx = int(round(ax + (bx - ax) * t))
        best = _route_candidate(world, cy, cx, last)
        if not pts or best != pts[-1]:
            pts.append(best)
            last = best
    return _smooth_route(pts)


def _route_candidate(world: "WorldState", cy: int, cx: int, last: tuple[int, int]) -> tuple[int, int]:
    best = (max(0, min(world.height - 1, cy)), max(0, min(world.width - 1, cx)))
    best_score = float("inf")
    slope = getattr(world, "terrain_slope", np.zeros_like(world.elevation))
    road = getattr(world, "road_access", None)
    if road is None:
        road = np.zeros_like(world.elevation)
    for yy in range(max(0, cy - 3), min(world.height, cy + 4)):
        for xx in range(max(0, cx - 3), min(world.width, cx + 4)):
            line_d = abs(yy - cy) + abs(xx - cx)
            progress = abs(yy - last[0]) + abs(xx - last[1])
            land_penalty = 3.2 if not bool(world.land_mask[yy, xx]) else 0.0
            water_penalty = float(world.water[yy, xx]) * 2.4
            score = (line_d * 0.36 + progress * 0.04
                     + max(0.0, float(world.elevation[yy, xx])) * 0.18
                     + float(slope[yy, xx]) * 4.6 + land_penalty + water_penalty
                     - float(road[yy, xx]) * 0.4)
            if score < best_score:
                best_score = score
                best = (yy, xx)
    return best


def _smooth_route(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if len(points) <= 2:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        a, b, c = out[-1], points[i], points[i + 1]
        if (a[0] - b[0], a[1] - b[1]) == (b[0] - c[0], b[1] - c[1]):
            continue
        out.append(b)
    out.append(points[-1])
    return out


def _road_access(world: "WorldState", roads: list[dict]) -> np.ndarray:
    access = np.zeros((world.height, world.width), dtype=np.float32)
    for road in roads:
        imp = float(road.get("importance", 0.3))
        for y, x in road.get("points", []):
            y = int(y); x = int(x)
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < world.height and 0 <= xx < world.width:
                        d = math.sqrt(dx * dx + dy * dy)
                        access[yy, xx] = max(access[yy, xx], imp * max(0.0, 1.0 - d / 4.0))
    return np.clip(access, 0.0, 1.0).astype(np.float32)


def _road_kind(a, b) -> str:
    trade = (a.wealth + b.wealth) / 140 + (getattr(a, "trade_dependency", 0.0)
                                           + getattr(b, "trade_dependency", 0.0)) * 0.25
    military = (getattr(a, "war_readiness", 0.0) + getattr(b, "war_readiness", 0.0)) * 0.35
    migration = getattr(a, "migration_pressure", 0.0) + getattr(b, "migration_pressure", 0.0)
    if military > max(trade, migration) and military > 0.42:
        return "military"
    if migration > trade and migration > 0.32:
        return "migration"
    if trade > 0.35:
        return "trade"
    return "local"


def _importance(a, b, kind: str) -> float:
    base = (a.population + b.population) / 42000.0 + (a.infrastructure + b.infrastructure) / 32.0
    if kind == "trade":
        base += 0.22
    elif kind == "military":
        base += 0.18
    elif kind == "migration":
        base += 0.12
    return float(np.clip(base, 0.12, 1.0))
