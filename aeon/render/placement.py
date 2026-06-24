"""Deterministic, collision-free city building layout.

The old renderer placed each building independently on a golden-angle spiral and then
**clamped the ring radius to the district envelope** — so in a dense district many
buildings collapsed onto the same radius and visibly stacked. This module replaces that
with a real layout pass: every building in a city is placed once, against a uniform
spatial-hash grid, rejecting any position that overlaps an already-placed footprint or a
radial road corridor. When a slot can't be found it shrinks the footprint, then pushes
outward, and only as a last resort flags `skip` (drawn as a collision-debug marker).

It is **deterministic** (all jitter comes from sha1 of stable ids, no RNG) and
**memoized per city** (keyed on the building set + radius) so repeated chunk requests are
cheap. Footprint and district-anchor logic are injected by `render/projection.py` so this
file stays free of any import cycle.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Callable

SPACING = 1.18          # min center gap between two footprints = (r_a + r_b) * SPACING
ROAD_CLEARANCE = 0.5    # keep building centers this far off a radial road trunk
ROAD_TRUNK = 0.72       # road runs center → this fraction of the district anchor
MAX_ATTEMPTS = 40
GOLDEN = math.pi * (3.0 - math.sqrt(5.0))

_ANCHOR_KINDS = {"temples", "market", "docks", "archives", "barracks", "noble_district"}
_IMPORTANCE = {
    "noble_district": 9, "temples": 8, "barracks": 7, "archives": 7, "docks": 6,
    "market": 6, "mines": 4, "workshops": 4, "farms": 3, "tavern": 2,
    "homes": 1, "slums": 0,
}
# how wide each district's building cluster spreads, as a fraction of city radius
_ENVELOPE = {"farmland": 0.36, "poor": 0.16, "residential": 0.19, "noble": 0.23,
             "market": 0.18, "waterfront": 0.2, "industrial": 0.22, "sacred": 0.2,
             "scholarly": 0.2, "military": 0.21}


def _sf(text: str) -> float:
    """Stable [0,1) hash — same idea as projection._stable_float."""
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def _seg_dist(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> float:
    """Distance from point (px,py) to the segment (ax,ay)->(bx,by)."""
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    if d2 <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / d2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def layout_city(city, footprint_fn: Callable, district_offset_fn: Callable) -> dict:
    """Return {building_id: {"x","y","r","skip"}} in city-local tile offsets.

    Memoized on the city; recomputed only when its building set or radius changes."""
    radius = float(getattr(city, "influence_radius", 4.0))
    ents = getattr(city, "building_entities", {}) or {}
    sig = (len(ents), round(radius, 1), hash(frozenset(ents.keys())))
    cached = getattr(city, "_render_layout", None)
    if cached is not None and cached[0] == sig:
        return cached[1]
    layout = _compute(city, ents, radius, footprint_fn, district_offset_fn)
    try:
        city._render_layout = (sig, layout)
    except Exception:  # noqa: BLE001 — never fail a render over a cache write
        pass
    return layout


def _compute(city, ents, radius, footprint_fn, district_offset_fn) -> dict:
    items = sorted(ents.values(),
                   key=lambda b: -_IMPORTANCE.get(b.kind, 1))   # anchors claim space first
    if not items:
        return {}
    fps = {b.id: float(footprint_fn(b.kind, b.district, getattr(b, "wealth", 0.0)))
           for b in items}
    max_fp = max(fps.values(), default=0.4)
    cell = max(0.5, max_fp * 2.0 * SPACING)

    # radial road trunks: center → 72% of each district anchor (keeps a clear avenue
    # without blocking the cluster that sits out at the anchor).
    roads = []
    for d in {b.district for b in items}:
        ax, ay = district_offset_fn(city.id, d, radius)
        roads.append((ax * ROAD_TRUNK, ay * ROAD_TRUNK))

    grid: dict[tuple[int, int], list[tuple[float, float, float]]] = {}

    def _cells(x, y):
        cx, cy = int(x // cell), int(y // cell)
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                yield (cx + i, cy + j)

    def _collides(x, y, r):
        for ck in _cells(x, y):
            for px, py, pr in grid.get(ck, ()):
                if (x - px) ** 2 + (y - py) ** 2 < ((r + pr) * SPACING) ** 2:
                    return True
        return False

    def _on_road(x, y, r):
        return any(_seg_dist(0.0, 0.0, rx, ry, x, y) < ROAD_CLEARANCE + r
                   for rx, ry in roads)

    layout: dict[str, dict[str, Any]] = {}
    for b in items:
        fp = fps[b.id]
        ax, ay = district_offset_fn(city.id, b.district, radius)
        phase = _sf(f"{city.id}:{b.district}:phase") * math.tau
        spacing = max(0.34, fp * 1.7)
        district_r = max(0.85, radius * _ENVELOPE.get(b.district, 0.2))
        is_anchor = b.kind in _ANCHOR_KINDS
        r = fp
        x = y = 0.0
        placed = False
        for attempt in range(MAX_ATTEMPTS):
            if is_anchor and attempt == 0:
                x, y = ax, ay                     # an anchor tries its district core first
            else:
                ring = min(math.sqrt(attempt + 0.4) * spacing,
                           district_r * (1.0 + attempt / MAX_ATTEMPTS))
                ang = phase + attempt * GOLDEN + _sf(f"{b.id}:{attempt}") * 0.6
                jit = (_sf(f"{b.id}:{attempt}:j") - 0.5) * spacing * 0.3
                x = ax + math.cos(ang) * (ring + jit)
                y = ay + math.sin(ang) * (ring + jit)
            if attempt == MAX_ATTEMPTS // 2:
                r = fp * 0.7                       # shrink to squeeze into a tight district
            if not _collides(x, y, r) and not _on_road(x, y, r):
                placed = True
                break
        skip = False
        if not placed:                            # push outward along a clear bearing
            r = fp * 0.7
            for k in range(16):
                ang = _sf(f"{b.id}:push:{k}") * math.tau
                rad = district_r + (k + 1) * spacing
                x, y = ax + math.cos(ang) * rad, ay + math.sin(ang) * rad
                if not _collides(x, y, r) and not _on_road(x, y, r):
                    placed = True
                    break
            if not placed:                        # genuinely overcrowded → flag, don't stack
                skip = True
                x, y = ax, ay
        if not skip:
            grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, r))
        layout[b.id] = {"x": x, "y": y, "r": r, "skip": skip}
    return layout
