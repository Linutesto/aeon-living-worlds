"""Tests for the collision-free city building layout (aeon/render/placement.py).

Guards the building-overlap fix: placed buildings must not intersect each other or the
radial roads, positions must be finite, dense cities must terminate, and overcrowding
must degrade gracefully (skip/shrink) rather than stack buildings on one spot.
"""

from __future__ import annotations

import math

from aeon.render.placement import (ROAD_CLEARANCE, ROAD_TRUNK, SPACING, _seg_dist,
                                   layout_city)
from aeon.render.projection import _building_footprint, _district_offset
from aeon.sim.cities import Building


class _City:
    def __init__(self, cid=1, radius=6.0):
        self.id = cid
        self.influence_radius = radius
        self.building_entities = {}


_DISTRICTS = ["residential", "market", "sacred", "scholarly", "industrial",
              "farmland", "waterfront", "military", "noble", "poor"]
_KINDS = ["homes", "market", "temples", "archives", "workshops", "farms",
          "docks", "barracks", "noble_district", "slums"]


def _city_with(n: int, *, district=None, radius=6.0):
    c = _City(radius=radius)
    for i in range(n):
        d = district or _DISTRICTS[i % len(_DISTRICTS)]
        kind = _KINDS[i % len(_KINDS)] if district is None else "homes"
        bid = f"building:{kind}:{i}"
        c.building_entities[bid] = Building(id=bid, kind=kind, city_id=c.id, district=d)
    return c


def _layout(city):
    return layout_city(city, _building_footprint, _district_offset)


def test_no_two_buildings_overlap():
    city = _city_with(80)
    layout = _layout(city)
    placed = [(s["x"], s["y"], s["r"]) for s in layout.values() if not s["skip"]]
    for i in range(len(placed)):
        xi, yi, ri = placed[i]
        for j in range(i + 1, len(placed)):
            xj, yj, rj = placed[j]
            d = math.hypot(xi - xj, yi - yj)
            assert d >= (ri + rj) * SPACING - 1e-6, "two buildings overlap"


def test_positions_are_finite():
    city = _city_with(120)
    for s in _layout(city).values():
        assert math.isfinite(s["x"]) and math.isfinite(s["y"]) and math.isfinite(s["r"])


def test_buildings_clear_roads():
    city = _city_with(80)
    layout = _layout(city)
    roads = []
    for d in {b.district for b in city.building_entities.values()}:
        ax, ay = _district_offset(city.id, d, city.influence_radius)
        roads.append((ax * ROAD_TRUNK, ay * ROAD_TRUNK))
    for s in layout.values():
        if s["skip"]:
            continue
        for rx, ry in roads:
            assert _seg_dist(0.0, 0.0, rx, ry, s["x"], s["y"]) >= ROAD_CLEARANCE + s["r"] - 1e-6


def test_dense_city_terminates_and_does_not_stack():
    # 200 buildings crammed into one small district = deliberate overcrowding.
    city = _city_with(200, district="residential", radius=4.0)
    layout = _layout(city)
    assert len(layout) == 200
    placed = [(s["x"], s["y"], s["r"]) for s in layout.values() if not s["skip"]]
    # whatever DID get placed must still be non-overlapping
    for i in range(len(placed)):
        xi, yi, ri = placed[i]
        for j in range(i + 1, len(placed)):
            xj, yj, rj = placed[j]
            assert math.hypot(xi - xj, yi - yj) >= (ri + rj) * SPACING - 1e-6


def test_layout_is_deterministic_and_memoized():
    city = _city_with(60)
    a = _layout(city)
    b = _layout(city)                         # served from the per-city cache
    assert a is b
    # a fresh city with the same buildings reproduces identical coordinates
    city2 = _city_with(60)
    c = _layout(city2)
    assert {k: (round(v["x"], 6), round(v["y"], 6)) for k, v in a.items()} == \
           {k: (round(v["x"], 6), round(v["y"], 6)) for k, v in c.items()}


def test_cache_invalidates_when_buildings_change():
    city = _city_with(20)
    _layout(city)
    bid = "building:temples:99"
    city.building_entities[bid] = Building(id=bid, kind="temples", city_id=city.id,
                                           district="sacred")
    layout2 = _layout(city)
    assert bid in layout2                      # new building got placed after change
