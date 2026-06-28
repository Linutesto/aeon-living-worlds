"""God-mode and natural cataclysms: meteors, ice ages, plagues, booms, anomalies.

Events are the dramatic, rare interventions — triggered either by the governor (via
a `trigger_event` directive) or by the player (God Console). Each event is applied
*through the parameters and grids*, never by scripting an outcome. Most install a
temporary modifier in `world.active_events` that decays over a number of ticks; some
are instantaneous shocks.

`apply(world, kind, **kw)` is the single entry point used by both the governor and
the server. `step(world)` ages active events and returns timeline entries when they
begin or end.
"""

from __future__ import annotations

import numpy as np

from . import world as _w
from . import species as _sp

# kind -> (human title, default duration in ticks)
CATALOG = {
    "meteor_impact":     ("Meteor Impact", 1),
    "ice_age":           ("Ice Age", 600),
    "plague":            ("Plague", 120),
    "resource_boom":     ("Resource Boom", 300),
    "magical_anomaly":   ("Magical Anomaly", 200),
    "volcanic_eruption": ("Volcanic Eruption", 1),
    "drought":           ("Great Drought", 250),
    "flood":             ("Great Flood", 1),
}


def apply(world: "_w.WorldState", kind: str, source: str = "governor", **kw) -> dict:
    """Trigger an event now. Returns the timeline event describing its onset."""
    if kind not in CATALOG:
        return {
            "tick": world.tick,
            "type": "event_rejected",
            "kind": kind,
            "title": "Unknown Event (Ignored)",
            "detail": f"Governor attempted invalid event {kind}, safely ignored."
        }

    title, duration = CATALOG[kind]
    duration = int(kw.get("duration", duration))

    handler = _HANDLERS[kind]
    detail = handler(world, **kw)

    if duration > 1:
        world.active_events.append({
            "kind": kind,
            "ticks_left": duration,
            "started": world.tick
        })

    return {
        "tick": world.tick,
        "type": "event",
        "kind": kind,
        "title": f"{title} ({source})",
        "detail": detail
    }
def step(world: "_w.WorldState") -> list[dict]:
    """Decay active events; emit a timeline entry when one ends."""
    out: list[dict] = []
    still: list[dict] = []
    for ev in world.active_events:
        ev["ticks_left"] -= 1
        _sustain(world, ev["kind"])
        if ev["ticks_left"] <= 0:
            title = CATALOG[ev["kind"]][0]
            out.append({"tick": world.tick, "type": "event_end", "kind": ev["kind"],
                        "title": f"{title} ended",
                        "detail": f"The {title.lower()} subsided after "
                                  f"{world.tick - ev['started']} ticks."})
        else:
            still.append(ev)
    world.active_events = still
    return out


# --- onset handlers: instantaneous shock or initial install ----------------------

def _meteor(world, **kw) -> str:
    rng = world.rng.stream("meteor")
    h, w = world.height, world.width
    cy, cx = int(rng.integers(0, h)), int(rng.integers(0, w))
    r = int(kw.get("radius", rng.integers(8, 16)))
    yy, xx = np.mgrid[0:h, 0:w]
    d2 = ((yy - cy) % h) ** 2 + ((xx - cx) % w) ** 2
    crater = np.exp(-d2 / (2 * r ** 2))
    world.elevation = np.clip(world.elevation - 0.6 * crater, -1, 1).astype(np.float32)
    world.food = np.clip(world.food - crater, 0, None).astype(np.float32)
    for sp in world.species.values():           # local mass casualty
        sy, sx = sp.pos
        if ((sy - cy) % h) ** 2 + ((sx - cx) % w) ** 2 < (r * 2) ** 2:
            sp.population *= 0.3
    for c in world.cities.values():              # nearby cities devastated
        if c.alive and (c.pos[0]-cy)**2 + (c.pos[1]-cx)**2 < (r*2)**2:
            c.population *= 0.4
            c.unrest = min(1.0, c.unrest + 0.5)
    world.add_marker("meteor", cy, cx, ttl=140, label="impact")
    return f"A meteor struck ({cy},{cx}), gouging a crater of radius {r}."


def _ice_age(world, **kw):
    world.params.set("temperature_bias", world.params.temperature_bias - 18)
    return "Temperatures plunged; the world entered an ice age."


def _plague(world, **kw):
    for sp in world.species.values():
        if sp.diet != _sp.PLANT:
            sp.population *= 0.6
    for c in world.cities.values():              # cities sicken and are marked
        if c.alive:
            c.plague = 120
            c.population *= 0.85
            c.unrest = min(1.0, c.unrest + 0.3)
            world.add_marker("plague", c.pos[0], c.pos[1], ttl=120, label=c.name)
    return "A plague swept through the living and the cities alike."


def _resource_boom(world, **kw):
    world.minerals *= 1.5
    world.energy *= 1.5
    world.params.adjust("resource_richness", +30)
    return "A surge of fertility and ore enriched the land."


def _anomaly(world, **kw):
    world.params.adjust("mutation_rate", +200)
    return "A strange anomaly warps the rules of life; mutations surge."


def _eruption(world, **kw):
    _w.terrain._erupt(world)
    world.params.set("temperature_bias", world.params.temperature_bias - 3)
    return "A great volcano erupted, raising new land and dimming the sky."


def _drought(world, **kw):
    world.params.set("rainfall_multiplier",
                     max(0.1, world.params.rainfall_multiplier * 0.4))
    return "The rains failed; a great drought begins."


def _flood(world, **kw):
    world.params.adjust("sea_level", +5)  # +5% of range
    return "Waters rose, drowning the lowlands."


_HANDLERS = {
    "meteor_impact": _meteor, "ice_age": _ice_age, "plague": _plague,
    "resource_boom": _resource_boom, "magical_anomaly": _anomaly,
    "volcanic_eruption": _eruption, "drought": _drought, "flood": _flood,
}


def _sustain(world, kind: str) -> None:
    """Per-tick upkeep for ongoing events (placeholder for richer dynamics)."""
    if kind == "drought":
        world.params.rainfall_multiplier = max(
            0.1, world.params.rainfall_multiplier * 0.999)
