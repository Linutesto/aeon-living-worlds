"""Tests for the upgraded building placement rules (focus area 1).

Extends test_placement with the new density controls: per-building-type minimum spacing,
density falloff from the city centre, slope-limit rejection, and the congestion/rejection
debug stats. The base no-overlap / road-clearance guarantees stay covered by
test_placement.py.
"""

from __future__ import annotations

import math

from aeon.render.placement import (DENSITY_FALLOFF, MAX_SLOPE, SPACING,
                                   layout_city, layout_stats)
from aeon.render.projection import _building_footprint, _district_offset
from aeon.sim.cities import Building


class _City:
    def __init__(self, cid=1, radius=7.0, **kw):
        self.id = cid
        self.influence_radius = radius
        self.building_entities = {}
        for k, v in kw.items():
            setattr(self, k, v)


def _city_of(kind, district, n, **kw):
    c = _City(**kw)
    for i in range(n):
        bid = f"{kind}:{i}"
        c.building_entities[bid] = Building(id=bid, kind=kind, city_id=c.id,
                                            district=district)
    return c


def _layout(c, slope_fn=None):
    return layout_city(c, _building_footprint, _district_offset, slope_fn=slope_fn)


def _min_spacing_ratio(layout):
    pts = [(s["x"], s["y"], s["r"]) for s in layout.values() if not s["skip"]]
    best = math.inf
    for i in range(len(pts)):
        xi, yi, ri = pts[i]
        for j in range(i + 1, len(pts)):
            xj, yj, rj = pts[j]
            d = math.hypot(xi - xj, yi - yj)
            best = min(best, d / max(1e-6, ri + rj))
    return best


def test_grand_buildings_get_more_spacing_than_homes():
    # temples carry a larger min-spacing multiplier than homes → their nearest-neighbour
    # gap (normalized by footprint) is wider.
    temples = _city_of("temples", "sacred", 40)
    homes = _city_of("homes", "residential", 40)
    assert _min_spacing_ratio(_layout(temples)) >= _min_spacing_ratio(_layout(homes)) - 1e-6
    assert _min_spacing_ratio(_layout(temples)) >= SPACING - 1e-6


def test_density_falloff_keeps_non_overlap():
    c = _city_of("homes", "residential", 120, density_falloff=1.2)
    layout = _layout(c)
    pts = [(s["x"], s["y"], s["r"]) for s in layout.values() if not s["skip"]]
    for i in range(len(pts)):
        xi, yi, ri = pts[i]
        for j in range(i + 1, len(pts)):
            xj, yj, rj = pts[j]
            assert math.hypot(xi - xj, yi - yj) >= (ri + rj) * SPACING - 1e-6


def test_slope_limit_avoids_steep_half_plane():
    c = _city_of("homes", "residential", 60)
    # everything on the +x side is a vertical wall — no building may settle there
    layout = _layout(c, slope_fn=lambda city, x, y: 0.3 if x > 0.5 else 0.0)
    on_wall = [s for s in layout.values() if not s["skip"] and s["x"] > 0.5]
    assert not on_wall, "no building may sit on a slope above MAX_SLOPE"


def test_all_steep_terrain_rejects_everything():
    c = _city_of("homes", "residential", 30)
    layout = _layout(c, slope_fn=lambda city, x, y: 0.5)   # nowhere is buildable
    assert all(s["skip"] for s in layout.values())
    st = layout_stats(c)
    assert st["slope_rejected"] >= 1
    assert st["placed"] == 0


def test_layout_stats_structure():
    c = _city_of("homes", "residential", 200, radius=4.0)   # deliberate overcrowding
    _layout(c)
    st = layout_stats(c)
    assert st["total"] == 200
    assert st["placed"] + st["rejected"] == 200
    assert 0.0 <= st["congestion"] <= 1.0
    assert st["avg_attempts"] >= 1.0
    assert sum(st["by_district"].values()) == 200


def test_constants_are_sane():
    assert 0.0 < MAX_SLOPE < 1.0
    assert DENSITY_FALLOFF >= 0.0
