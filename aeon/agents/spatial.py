"""Spatial intelligence for materialized citizens.

The statistical population can be 10k-100k+, but only the LOD persona pool is fully
embodied. This module gives those real `Person` objects compact world observations,
terrain-aware target selection, and bounded pathfinding without changing city/pop
economy semantics.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from typing import Any

import numpy as np

OCEAN = 0
MOUNTAIN = 5
SNOW = 6

SPATIAL_FEATURES = [
    "distance_to_home", "distance_to_work", "distance_to_food", "distance_to_enemy",
    "distance_to_city_center", "terrain_risk", "crowd_density", "road_access",
    "local_economy", "local_health", "local_war_pressure", "local_famine_pressure",
    "temperature_stress", "social_nearby_count", "faction_alignment_nearby",
    "migration_opportunity_score", "safety_score", "at_target",
    # multiplied spatial data sources (embodied influence fields): the citizen feels
    # markets/temples/war fronts/famine zones/migration pull/religion at its location.
    "war_front_proximity", "famine_zone_proximity", "religion_influence",
    "market_proximity", "temple_proximity", "migration_path_score",
]

# Embodied movement intents the spec asks for: a coarse "why am I moving" label derived
# from the chosen life action. Surfaced on every target so the renderer/UI and any
# learned policy can reason about destinations in body-space, not just abstract state.
MOVEMENT_INTENTS = ["go_home", "go_work", "flee", "migrate", "seek_food", "visit_market",
                    "visit_temple", "join_army", "trade", "socialize", "rest",
                    "study", "wander"]
_ACTION_TO_INTENT = {
    "rest": "rest", "seek_shelter": "flee", "flee": "flee", "migrate": "migrate",
    "feed": "seek_food", "venture": "seek_food", "work": "go_work", "study": "study",
    "worship": "visit_temple", "trade": "trade", "join_army": "join_army",
    "socialize": "socialize", "court": "socialize", "feud": "socialize",
    "visit_city_center": "visit_market",
}


def movement_intent_for(action: str) -> str:
    """Map a life action to its embodied movement intent (see MOVEMENT_INTENTS)."""
    return _ACTION_TO_INTENT.get(action, "wander")

TARGET_KIND_BY_ACTION = {
    "work": "workplace",
    "feed": "food",
    "study": "school",
    "migrate": "city",
    "worship": "temple",
    "socialize": "citizen",
    "flee": "shelter",
    "trade": "market",
    "court": "citizen",
    "join_army": "fort",
    "seek_shelter": "shelter",
    "visit_city_center": "city_center",
    "feud": "citizen",
    "rest": "home",
    "venture": "resource",
}


class SpatialIndex:
    """Tiny grid index rebuilt for materialized citizens on life ticks."""

    def __init__(self, cell: int = 4) -> None:
        self.cell = max(1, int(cell))
        self.cells: dict[tuple[int, int], list] = defaultdict(list)

    def rebuild(self, persons) -> None:
        self.cells.clear()
        for p in persons:
            if not getattr(p, "alive", False):
                continue
            y, x = current_tile(p)
            self.cells[(y // self.cell, x // self.cell)].append(p)

    def nearby(self, y: int, x: int, radius: int) -> list:
        r = max(1, int(math.ceil(radius / self.cell)))
        cy, cx = y // self.cell, x // self.cell
        out = []
        r2 = radius * radius
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                for p in self.cells.get((yy, xx), []):
                    py, px = current_tile(p)
                    if (py - y) * (py - y) + (px - x) * (px - x) <= r2:
                        out.append(p)
        return out


def clamp_tile(world, y: float, x: float) -> tuple[int, int]:
    return (max(0, min(world.height - 1, int(round(y)))),
            max(0, min(world.width - 1, int(round(x)))))


def current_tile(p) -> tuple[int, int]:
    pos = getattr(p, "position", None)
    if pos:
        return int(round(pos[0])), int(round(pos[1]))
    tile = getattr(p, "current_tile", None)
    if tile:
        return int(tile[0]), int(tile[1])
    return (0, 0)


def set_position(p, y: float, x: float, world=None) -> None:
    if world is not None:
        y, x = clamp_tile(world, y, x)
    p.position = (float(y), float(x))
    p.current_tile = (int(round(y)), int(round(x)))


def building_position(world, city, building_id: str) -> tuple[float, float] | None:
    if not city or not building_id:
        return None
    b = getattr(city, "building_entities", {}).get(building_id)
    if not b:
        return None
    # Import lazily to avoid a render->agents import cycle at module import time.
    from ..render.projection import _layout_offset
    ox, oy, _, _ = _layout_offset(city, b)
    y = city.pos[0] + oy
    x = city.pos[1] + ox
    return tuple(float(v) for v in clamp_tile(world, y, x))


def initialize_person_position(world, p, city) -> None:
    home = building_position(world, city, getattr(p, "home_building", ""))
    work = building_position(world, city, getattr(p, "work_building", ""))
    if home is None and city is not None:
        home = city.pos
    if home is None:
        home = current_tile(p)
    p.home_position = (float(home[0]), float(home[1]))
    p.work_position = (float(work[0]), float(work[1])) if work else p.home_position
    set_position(p, *p.home_position, world=world)


def terrain_risk(world, y: int, x: int) -> float:
    if not (0 <= y < world.height and 0 <= x < world.width):
        return 1.0
    water = float(world.water[y, x])
    biome = int(world.biome[y, x])
    temp = float(world.temperature[y, x])
    risk = 0.0
    risk += 0.8 if biome == OCEAN or not bool(world.land_mask[y, x]) else 0.0
    risk += min(0.4, water * 0.65)
    risk += min(0.45, slope_at(world, y, x) * 4.0)
    risk += 0.18 if biome == MOUNTAIN else 0.0
    risk += 0.12 if biome == SNOW else 0.0
    risk += max(0.0, abs(temp - 18.0) - 14.0) / 45.0
    return max(0.0, min(1.0, risk))


def slope_at(world, y: int, x: int) -> float:
    here = float(world.elevation[y, x])
    vals = (
        float(world.elevation[max(0, y - 1), x]),
        float(world.elevation[min(world.height - 1, y + 1), x]),
        float(world.elevation[y, max(0, x - 1)]),
        float(world.elevation[y, min(world.width - 1, x + 1)]),
    )
    return max(abs(here - v) for v in vals)


def passable(world, y: int, x: int, *, allow_water: bool = False) -> bool:
    if not (0 <= y < world.height and 0 <= x < world.width):
        return False
    if allow_water:
        return True
    if not bool(world.land_mask[y, x]) or int(world.biome[y, x]) == OCEAN:
        return False
    return float(world.water[y, x]) < 0.78


def travel_cost(world, y: int, x: int, road_hint: float = 0.0,
                *, allow_water: bool = False) -> float:
    if not passable(world, y, x, allow_water=allow_water):
        return 9999.0
    cost = 1.0 + terrain_risk(world, y, x) * 3.0 + slope_at(world, y, x) * 8.0
    cost -= max(0.0, min(0.45, road_hint))
    return max(0.25, cost)


def pathfind(world, start: tuple[int, int], goal: tuple[int, int], *,
             allow_water: bool = False, max_nodes: int = 900) -> tuple[list[tuple[int, int]], bool]:
    """Bounded A* on the world grid. Falls back to terrain-steered line when too far."""
    sy, sx = clamp_tile(world, *start)
    gy, gx = nearest_passable(world, *goal, allow_water=allow_water)
    if (sy, sx) == (gy, gx):
        return [(sy, sx)], True
    manhattan = abs(sy - gy) + abs(sx - gx)
    if manhattan > 80:
        pts = steered_route(world, (sy, sx), (gy, gx), allow_water=allow_water)
        return pts, bool(pts and pts[-1] == (gy, gx))
    openq = [(0.0, (sy, sx))]
    came: dict[tuple[int, int], tuple[int, int]] = {}
    gscore = {(sy, sx): 0.0}
    visited = 0
    while openq and visited < max_nodes:
        _, cur = heapq.heappop(openq)
        visited += 1
        if cur == (gy, gx):
            return _reconstruct(came, cur), True
        cy, cx = cur
        for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1),
                       (cy - 1, cx - 1), (cy - 1, cx + 1), (cy + 1, cx - 1), (cy + 1, cx + 1)):
            if not passable(world, ny, nx, allow_water=allow_water):
                continue
            step = math.sqrt(2.0) if ny != cy and nx != cx else 1.0
            ng = gscore[cur] + travel_cost(world, ny, nx, allow_water=allow_water) * step
            if ng >= gscore.get((ny, nx), float("inf")):
                continue
            came[(ny, nx)] = cur
            gscore[(ny, nx)] = ng
            h = abs(ny - gy) + abs(nx - gx)
            heapq.heappush(openq, (ng + h, (ny, nx)))
    pts = steered_route(world, (sy, sx), (gy, gx), allow_water=allow_water)
    return pts, bool(pts and pts[-1] == (gy, gx))


def nearest_passable(world, y: int, x: int, *, allow_water: bool = False) -> tuple[int, int]:
    y, x = clamp_tile(world, y, x)
    if passable(world, y, x, allow_water=allow_water):
        return y, x
    for r in range(1, 8):
        best = None
        best_risk = float("inf")
        for yy in range(max(0, y - r), min(world.height, y + r + 1)):
            for xx in range(max(0, x - r), min(world.width, x + r + 1)):
                if not passable(world, yy, xx, allow_water=allow_water):
                    continue
                risk = terrain_risk(world, yy, xx) + abs(yy - y) * 0.02 + abs(xx - x) * 0.02
                if risk < best_risk:
                    best_risk = risk
                    best = (yy, xx)
        if best:
            return best
    return y, x


def steered_route(world, start: tuple[int, int], goal: tuple[int, int], *,
                  allow_water: bool = False) -> list[tuple[int, int]]:
    sy, sx = start
    gy, gx = goal
    dist = max(1, abs(sy - gy) + abs(sx - gx))
    steps = max(2, min(36, int(dist / 4)))
    pts = [nearest_passable(world, sy, sx, allow_water=allow_water)]
    last = pts[0]
    for i in range(1, steps + 1):
        t = i / steps
        cy = int(round(sy + (gy - sy) * t))
        cx = int(round(sx + (gx - sx) * t))
        best = nearest_passable(world, cy, cx, allow_water=allow_water)
        best_score = float("inf")
        for yy in range(max(0, cy - 3), min(world.height, cy + 4)):
            for xx in range(max(0, cx - 3), min(world.width, cx + 4)):
                if not passable(world, yy, xx, allow_water=allow_water):
                    continue
                score = (
                    abs(yy - cy) * 0.35 + abs(xx - cx) * 0.35
                    + abs(yy - last[0]) * 0.03 + abs(xx - last[1]) * 0.03
                    + terrain_risk(world, yy, xx) * 2.2
                    + slope_at(world, yy, xx) * 5.0
                )
                if score < best_score:
                    best_score = score
                    best = (yy, xx)
        if best != pts[-1]:
            pts.append(best)
            last = best
    if pts[-1] != nearest_passable(world, gy, gx, allow_water=allow_water):
        pts.append(nearest_passable(world, gy, gx, allow_water=allow_water))
    return pts


def _reconstruct(came: dict, cur: tuple[int, int]) -> list[tuple[int, int]]:
    out = [cur]
    while cur in came:
        cur = came[cur]
        out.append(cur)
    out.reverse()
    return out


def choose_target(world, population, p, action: str, city=None,
                  spatial_index: SpatialIndex | None = None) -> dict[str, Any]:
    city = city or (world.cities.get(p.home_city) if getattr(p, "home_city", None) else None)
    kind = TARGET_KIND_BY_ACTION.get(action, "city_center")
    reason = action
    target_id = None
    pos = None
    if action == "migrate":
        dest = best_migration_city(world, city, p)
        if dest:
            kind, target_id, pos = "city", f"city:{dest.id}", dest.pos
            reason = "famine + unrest + opportunity" if city and (city.famine or city.unrest > 0.35) else "opportunity"
    elif action == "feed":
        pos = nearest_resource(world, *current_tile(p))
        kind, target_id = "food", "resource:food"
    elif action in ("work", "study", "worship", "trade", "join_army"):
        bid = p.work_building
        if action == "worship":
            bid = _building_of_kind(city, "temples") or p.work_building
        elif action == "study":
            bid = _building_of_kind(city, "archives") or p.work_building
        elif action == "trade":
            bid = _building_of_kind(city, "market") or p.work_building
        elif action == "join_army":
            bid = _building_of_kind(city, "barracks") or p.work_building
        pos = building_position(world, city, bid) if city else None
        target_id = f"building:{bid}" if bid else None
    elif action in ("socialize", "court", "feud"):
        other = _social_target(population, p, action, city, spatial_index)
        if other:
            kind, target_id, pos = "citizen", f"person:{other.id}", current_tile(other)
    elif action in ("rest", "seek_shelter", "flee"):
        bid = p.home_building or (_building_of_kind(city, "homes") if city else "")
        pos = building_position(world, city, bid) if city else None
        target_id = f"building:{bid}" if bid else None
        kind = "shelter" if action in ("seek_shelter", "flee") else "home"
    elif action == "venture":
        pos = nearest_resource(world, *current_tile(p))
        kind, target_id = "resource", "resource:food"
    if pos is None and city:
        pos = city.pos
        target_id = f"city:{city.id}"
        kind = "city_center"
    if pos is None:
        pos = current_tile(p)
    ty, tx = clamp_tile(world, pos[0], pos[1])
    urgency = _urgency(p, city, action)
    return {
        "type": action,
        "target_kind": kind,
        "target_id": target_id,
        "target_position": [float(tx), float(ty), float(world.elevation[ty, tx])],
        "movement_intent": movement_intent_for(action),
        "urgency": round(urgency, 3),
        "expected_reward": round(_expected_reward(p, city, action), 3),
        "reason": reason,
    }


def begin_movement(world, p, action_obj: dict[str, Any], counters: dict[str, float] | None = None) -> None:
    start = current_tile(p)
    target = action_obj.get("target_position") or [start[1], start[0], 0.0]
    goal = clamp_tile(world, float(target[1]), float(target[0]))
    path, ok = pathfind(world, start, goal, allow_water=False)
    p.current_action = action_obj
    p.path = [(float(y), float(x)) for y, x in path[:96]]
    p.path_index = 0
    p.path_progress = 0.0
    p.destination = (float(goal[0]), float(goal[1]))
    p.moving = len(path) > 1
    if counters is not None:
        counters["paths_requested"] = counters.get("paths_requested", 0) + 1
        counters["path_failed"] = counters.get("path_failed", 0) + (0 if ok else 1)
        counters["path_length_sum"] = counters.get("path_length_sum", 0) + max(0, len(path) - 1)


def advance_movement(world, p, *, speed: float = 0.9) -> dict[str, Any] | None:
    if not getattr(p, "moving", False) or len(getattr(p, "path", [])) < 2:
        return None
    path = p.path
    idx = int(getattr(p, "path_index", 0))
    if idx >= len(path) - 1:
        p.moving = False
        return _arrival(world, p)
    ay, ax = path[idx]
    by, bx = path[idx + 1]
    dist = max(0.001, math.hypot(by - ay, bx - ax))
    progress = float(getattr(p, "path_progress", 0.0)) + speed / dist
    while progress >= 1.0 and idx < len(path) - 1:
        idx += 1
        progress -= 1.0
        if idx >= len(path) - 1:
            set_position(p, path[-1][0], path[-1][1], world=world)
            p.path_index = len(path) - 1
            p.path_progress = 0.0
            p.moving = False
            return _arrival(world, p)
        ay, ax = path[idx]
        by, bx = path[idx + 1]
        dist = max(0.001, math.hypot(by - ay, bx - ax))
    ny = ay + (by - ay) * progress
    nx = ax + (bx - ax) * progress
    set_position(p, ny, nx, world=world)
    p.path_index = idx
    p.path_progress = progress
    return None


def observation(world, population, p, city=None,
                spatial_index: SpatialIndex | None = None) -> dict[str, Any]:
    city = city or (world.cities.get(p.home_city) if getattr(p, "home_city", None) else None)
    y, x = current_tile(p)
    near = spatial_index.nearby(y, x, getattr(p, "perception_radius", 8)) if spatial_index else []
    near = [q for q in near if q.id != p.id]
    nearest_city = nearest_city_to(world, y, x)
    nearest_food = nearest_resource(world, y, x)
    enemy_d = nearest_enemy_distance(world, city, y, x)
    home = getattr(p, "home_position", (y, x))
    work = getattr(p, "work_position", home)
    d_home = _norm_dist(world, y, x, home[0], home[1])
    d_work = _norm_dist(world, y, x, work[0], work[1])
    d_food = _norm_dist(world, y, x, nearest_food[0], nearest_food[1]) if nearest_food else 1.0
    d_city = _norm_dist(world, y, x, nearest_city.pos[0], nearest_city.pos[1]) if nearest_city else 1.0
    local_health = 1.0 - max(getattr(city, "famine_risk", 0.0), getattr(city, "unrest", 0.0),
                             getattr(city, "damage", 0.0)) if city else 0.5
    faction_align = _faction_alignment(p, near)
    road_access = 1.0 - min(1.0, d_city * 2.0)
    safety = max(0.0, min(1.0, local_health * 0.55 + (1.0 - terrain_risk(world, y, x)) * 0.35
                          + min(1.0, enemy_d) * 0.1))
    # ---- embodied influence fields (new spatial data sources) ----
    war_front = max(_war_pressure(world, city), max(0.0, 1.0 - enemy_d))
    famine_zone = _famine_zone_proximity(world, city, y, x)
    religion_influence = max(0.0, min(1.0,
        0.5 * (1.0 if getattr(p, "religion_id", None) else 0.0)
        + 0.5 * float((getattr(p, "ideology", None) or {}).get("piety", 0.0))))
    market_prox = _building_proximity(world, city, "market", y, x)
    temple_prox = _building_proximity(world, city, "temples", y, x)
    migration_path = migration_opportunity(world, city)
    obs = {
        "tile": [y, x],
        "position": [float(x), float(y), float(world.elevation[y, x])],
        "home_position": [float(home[1]), float(home[0])],
        "work_position": [float(work[1]), float(work[0])],
        "nearest_city": nearest_city.id if nearest_city else None,
        "nearest_road": city.id if city else None,
        "nearest_food": [float(nearest_food[1]), float(nearest_food[0])] if nearest_food else None,
        "nearby_citizens": len(near),
        "nearby_buildings": _nearby_building_count(city, y, x),
        "nearby_territory": city.civ_id if city else None,
        "terrain_type": int(world.biome[y, x]),
        "altitude": round(float(world.elevation[y, x]), 4),
        "slope": round(slope_at(world, y, x), 4),
        "water": round(float(world.water[y, x]), 4),
        "snow": int(world.biome[y, x]) == SNOW,
        "temperature": round(float(world.temperature[y, x]), 2),
        "economy_pressure": round(1.0 - getattr(city, "economic_health", 1.0), 3) if city else 0.0,
        "health_pressure": round(1.0 - local_health, 3),
        "danger_zones": _danger_zones(world, city, y, x),
        "travel_cost": round(travel_cost(world, y, x), 3),
        "visibility_radius": getattr(p, "perception_radius", 8),
        "features": {
            "distance_to_home": d_home,
            "distance_to_work": d_work,
            "distance_to_food": d_food,
            "distance_to_enemy": min(1.0, enemy_d),
            "distance_to_city_center": d_city,
            "terrain_risk": terrain_risk(world, y, x),
            "crowd_density": min(1.0, len(near) / 12.0),
            "road_access": road_access,
            "local_economy": min(1.0, getattr(city, "economic_health", 0.5)) if city else 0.5,
            "local_health": max(0.0, min(1.0, local_health)),
            "local_war_pressure": _war_pressure(world, city),
            "local_famine_pressure": min(1.0, getattr(city, "famine_risk", 0.0)
                                         + (1.0 if city and city.famine > 0 else 0.0)),
            "temperature_stress": max(0.0, min(1.0, abs(float(world.temperature[y, x]) - 18.0) / 35.0)),
            "social_nearby_count": min(1.0, len(near) / 8.0),
            "faction_alignment_nearby": faction_align,
            "migration_opportunity_score": migration_opportunity(world, city),
            "safety_score": safety,
            "at_target": 0.0 if getattr(p, "moving", False) else 1.0,
            "war_front_proximity": war_front,
            "famine_zone_proximity": famine_zone,
            "religion_influence": religion_influence,
            "market_proximity": market_prox,
            "temple_proximity": temple_prox,
            "migration_path_score": migration_path,
        },
    }
    return obs


def _building_proximity(world, city, kind: str, y: int, x: int) -> float:
    """Closeness (1=on top, 0=far/none) of the citizen to the nearest building of a kind."""
    if not city:
        return 0.0
    bid = _building_of_kind(city, kind)
    bp = building_position(world, city, bid) if bid else None
    if not bp:
        return 0.0
    return max(0.0, 1.0 - _norm_dist(world, y, x, bp[0], bp[1]) * 3.0)


def _famine_zone_proximity(world, city, y: int, x: int) -> float:
    """Closeness to an active famine/plague zone — own city plus nearby world markers."""
    fam = getattr(city, "famine_risk", 0.0) if city else 0.0
    if city and city.famine > 0:
        fam = max(fam, 0.85)
    for m in world.markers[-80:]:
        if m.get("kind") in ("famine", "plague"):
            d = math.hypot(float(m.get("y", y)) - y, float(m.get("x", x)) - x)
            fam = max(fam, max(0.0, 1.0 - d / 14.0))
    return max(0.0, min(1.0, fam))


def spatial_feature_vector(world, population, p, city=None,
                           spatial_index: SpatialIndex | None = None) -> list[float]:
    obs = observation(world, population, p, city, spatial_index)
    feats = obs["features"]
    return [float(max(0.0, min(1.0, feats[name]))) for name in SPATIAL_FEATURES]


def compact_observation(world, population, p, city=None,
                        spatial_index: SpatialIndex | None = None) -> dict[str, Any]:
    obs = observation(world, population, p, city, spatial_index)
    return {
        "tile": obs["tile"],
        "position": obs["position"],
        "terrain": obs["terrain_type"],
        "nearest_city": obs["nearest_city"],
        "nearby_citizens": obs["nearby_citizens"],
        "danger": obs["danger_zones"],
        "features": {k: round(v, 3) for k, v in obs["features"].items()},
    }


def best_migration_city(world, city, p):
    options = [c for c in world.cities.values()
               if c.alive and (city is None or c.id != city.id) and c.famine == 0]
    if not options:
        return None
    y, x = city.pos if city else current_tile(p)
    def score(c):
        d = abs(c.pos[0] - y) + abs(c.pos[1] - x)
        return (getattr(c, "economic_health", 1.0) * 0.45
                + getattr(c, "civic_stability", 1.0) * 0.25
                + min(1.0, c.wealth / 80) * 0.18
                - getattr(c, "famine_risk", 0.0) * 0.35
                - d / max(world.width, world.height) * 0.3)
    return max(options, key=score)


def migration_opportunity(world, city) -> float:
    dest = best_migration_city(world, city, None) if city else None
    if not dest:
        return 0.0
    base = getattr(dest, "economic_health", 1.0) * 0.5 + getattr(dest, "civic_stability", 1.0) * 0.3
    if city:
        base += max(0.0, getattr(city, "famine_risk", 0.0) - getattr(dest, "famine_risk", 0.0)) * 0.4
    return max(0.0, min(1.0, base))


def nearest_city_to(world, y: int, x: int):
    live = [c for c in world.cities.values() if c.alive]
    if not live:
        return None
    return min(live, key=lambda c: abs(c.pos[0] - y) + abs(c.pos[1] - x))


def nearest_resource(world, y: int, x: int) -> tuple[int, int] | None:
    radius = 8
    best = None
    best_score = -1.0
    for yy in range(max(0, y - radius), min(world.height, y + radius + 1)):
        for xx in range(max(0, x - radius), min(world.width, x + radius + 1)):
            if not passable(world, yy, xx):
                continue
            score = float(world.food[yy, xx]) - (abs(yy - y) + abs(xx - x)) * 0.015
            if score > best_score:
                best_score = score
                best = (yy, xx)
    return best


def nearest_enemy_distance(world, city, y: int, x: int) -> float:
    if not city:
        return 1.0
    d = 1.0
    for u in world.units.values():
        if getattr(u, "kind", "") != "army" or getattr(u, "civ_id", city.civ_id) == city.civ_id:
            continue
        uy, ux = getattr(u, "pos", (y, x))
        d = min(d, _norm_dist(world, y, x, uy, ux))
    return d


def _norm_dist(world, y1: float, x1: float, y2: float, x2: float) -> float:
    return max(0.0, min(1.0, math.hypot(y1 - y2, x1 - x2) / max(1.0, math.hypot(world.height, world.width))))


def _building_of_kind(city, kind: str) -> str:
    if not city:
        return ""
    for bid, b in getattr(city, "building_entities", {}).items():
        if not getattr(b, "abandoned", False) and getattr(b, "kind", "") == kind:
            return bid
    return ""


def _social_target(population, p, action: str, city, spatial_index: SpatialIndex | None):
    ids = []
    if action == "court" and p.partner_id:
        ids = [p.partner_id]
    elif action == "feud":
        ids = [oid for oid, r in p.relationships.items() if r.strength < 0]
    else:
        ids = [oid for oid, r in p.relationships.items() if r.strength > 0]
    for oid in ids:
        q = population.get(oid) if population is not None else None
        if q and q.alive:
            return q
    if spatial_index:
        y, x = current_tile(p)
        near = [q for q in spatial_index.nearby(y, x, 10) if q.id != p.id and q.alive]
        if near:
            return near[0]
    if city:
        res = [q for q in population.residents(city.id) if q.id != p.id and q.alive] \
            if population is not None else []
        return res[0] if res else None
    return None


def _urgency(p, city, action: str) -> float:
    u = p.stress * 0.35 + (1.0 - p.health) * 0.35
    if city:
        u += getattr(city, "famine_risk", 0.0) * 0.25 + city.unrest * 0.2
        if city.famine > 0 or city.plague > 0:
            u += 0.25
    if action in ("migrate", "flee", "seek_shelter"):
        u += 0.2
    return max(0.0, min(1.0, u))


def _expected_reward(p, city, action: str) -> float:
    base = {
        "work": p.skills.get("trade", 0.2) * 0.3 + p.skills.get("farming", 0.2) * 0.3,
        "study": p.goals.get("knowledge", 0.2),
        "worship": p.goals.get("faith", 0.2),
        "migrate": 0.45,
        "socialize": p.personality.get("extraversion", 0.5),
        "court": p.goals.get("family", 0.3),
        "rest": 1.0 - p.health,
    }.get(action, 0.35)
    if city:
        base += getattr(city, "economic_health", 1.0) * 0.15
        base -= getattr(city, "famine_risk", 0.0) * 0.2
    return max(0.0, min(1.0, base))


def _arrival(world, p) -> dict[str, Any]:
    action = getattr(p, "current_action", {}) or {}
    return {"tick": world.tick, "type": "movement",
            "title": f"{p.name} arrived at {action.get('target_kind', 'destination')}",
            "detail": f"{p.name} reached {action.get('target_id') or 'a destination'}.",
            "person_id": p.id, "city_id": getattr(p, "home_city", None),
            "action": action.get("type"), "target_kind": action.get("target_kind"),
            "movement": {"success": True, "path_length": len(getattr(p, "path", []))}}


def _faction_alignment(p, near) -> float:
    if not near:
        return 0.5
    mine = set(getattr(p, "faction_ids", []) or [])
    if not mine:
        return 0.5
    aligned = 0
    for q in near:
        if mine.intersection(getattr(q, "faction_ids", []) or []):
            aligned += 1
    return max(0.0, min(1.0, aligned / max(1, len(near))))


def _nearby_building_count(city, y: int, x: int) -> int:
    if not city:
        return 0
    n = 0
    for b in getattr(city, "building_entities", {}).values():
        if getattr(b, "abandoned", False):
            continue
        # Cheap approximation: buildings are inside city influence.
        if abs(city.pos[0] - y) + abs(city.pos[1] - x) <= max(3, int(city.influence_radius)):
            n += 1
    return min(32, n)


def _danger_zones(world, city, y: int, x: int) -> list[dict[str, Any]]:
    out = []
    if city and (city.famine > 0 or city.plague > 0 or city.unrest > 0.55):
        out.append({"kind": "city_crisis", "city_id": city.id,
                    "pressure": round(max(city.unrest, getattr(city, "famine_risk", 0.0),
                                          1.0 if city.plague > 0 else 0.0), 3)})
    for m in world.markers[-80:]:
        d = math.hypot(float(m.get("y", y)) - y, float(m.get("x", x)) - x)
        if d <= 12 and m.get("kind") in {"battle", "famine", "plague", "meteor", "volcano"}:
            out.append({"kind": m.get("kind"), "distance": round(d, 2)})
    return out[:4]


def _war_pressure(world, city) -> float:
    if not city:
        return 0.0
    return 1.0 if any(getattr(u, "kind", "") == "army" and getattr(u, "dest_city", None) == city.id
                      for u in world.units.values()) else min(1.0, getattr(city, "war_readiness", 0.0))
