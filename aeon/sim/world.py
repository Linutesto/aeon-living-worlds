"""WorldState — the single container for everything that exists, plus the master
tick() that advances the world one deterministic step.

Tick order matters and is fixed:
    terrain → climate → resources → species → evolution → civilization
            → cities → units → events → marker decay

The living human layer is cities (where people live) and units (people moving:
traders, armies, migrants, explorers, civilians). World-space `markers` carry
transient events (battles, famines, plagues, disasters) so they are *seen* in the
world, not buried in logs.

Each subsystem is a module-level `step(world)` function that mutates `world` in
place using `world.rng` and `world.params`. This file owns *only* sequencing and
the state container, never the rules themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ..rng import RNG
from .params import WorldParams

if TYPE_CHECKING:
    from .species import Species
    from .civilization import Civilization
    from .cities import City
    from .units import Unit


# Biome ids used across sim + dashboard. Keep in sync with web/js/world3d.js.
# Defined before the submodule imports below: resources.py reads it at import time.
BIOME = {
    "ocean": 0, "beach": 1, "grassland": 2, "forest": 3,
    "desert": 4, "mountain": 5, "snow": 6, "swamp": 7, "tundra": 8,
}

from . import (terrain, climate, resources, species, evolution,  # noqa: E402
               civilization, cities, units, events)


@dataclass
class WorldState:
    cfg: "object"                       # the loaded Config (avoids a circular import)
    rng: RNG
    params: WorldParams
    width: int
    height: int

    tick: int = 0

    # --- terrain layers (float grids, h×w) ---
    elevation: np.ndarray = None        # type: ignore[assignment]  -1..1
    water: np.ndarray = None            # type: ignore[assignment]  standing water depth
    biome: np.ndarray = None            # type: ignore[assignment]  int ids from BIOME

    # --- climate layers ---
    temperature: np.ndarray = None      # type: ignore[assignment]  degrees C
    humidity: np.ndarray = None         # type: ignore[assignment]  0..1
    rainfall: np.ndarray = None         # type: ignore[assignment]  0..1

    # --- resource layers ---
    minerals: np.ndarray = None         # type: ignore[assignment]
    food: np.ndarray = None             # type: ignore[assignment]
    energy: np.ndarray = None           # type: ignore[assignment]

    # --- living things ---
    species: dict[int, "Species"] = field(default_factory=dict)
    civilizations: dict[int, "Civilization"] = field(default_factory=dict)
    cities: dict[int, "City"] = field(default_factory=dict)
    units: dict[int, "Unit"] = field(default_factory=dict)

    # --- transient world-space events (battles, famines, disasters) ---
    markers: list[dict] = field(default_factory=list)

    # --- persistent historical terrain/city evidence derived from real events ---
    historical_sites: list[dict] = field(default_factory=list)

    # --- god-mode / natural events currently in effect ---
    active_events: list[dict] = field(default_factory=list)

    # --- founding events from genesis (drained into the timeline by the engine) ---
    genesis_events: list[dict] = field(default_factory=list)

    # monotonic id sources
    _next_species_id: int = 1
    _next_civ_id: int = 1
    _next_city_id: int = 1
    _next_unit_id: int = 1

    def new_species_id(self) -> int:
        i = self._next_species_id
        self._next_species_id += 1
        return i

    def new_civ_id(self) -> int:
        i = self._next_civ_id
        self._next_civ_id += 1
        return i

    def new_city_id(self) -> int:
        i = self._next_city_id
        self._next_city_id += 1
        return i

    def new_unit_id(self) -> int:
        i = self._next_unit_id
        self._next_unit_id += 1
        return i

    def add_marker(self, kind: str, y: float, x: float, ttl: int = 80,
                   label: str = "") -> None:
        self.markers.append({"kind": kind, "y": float(y), "x": float(x),
                             "born": self.tick, "ttl": ttl, "label": label})

    @property
    def urban_population(self) -> int:
        return int(sum(c.population for c in self.cities.values() if c.alive))

    @property
    def population(self) -> int:
        return int(sum(s.population for s in self.species.values()))

    @property
    def land_mask(self) -> np.ndarray:
        return self.elevation > self.params.sea_level


def create_world(cfg, params: "WorldParams | None" = None) -> WorldState:
    """Genesis. Builds the grids and seeds initial life.

    `params` lets a restart inject player-chosen generation knobs (sea level,
    resource richness, carrying capacity, …) *before* seeding — genesis reads them, so
    they shape the world deterministically. Omitted ⇒ defaults (original behavior)."""
    rng = RNG(cfg.world.seed)
    world = WorldState(
        cfg=cfg,
        rng=rng,
        params=params if params is not None else WorldParams.from_defaults(),
        width=cfg.world.width,
        height=cfg.world.height,
    )
    terrain.generate(world)     # initial heightmap, oceans, rivers, caves
    climate.initialize(world)   # initial temperature/humidity fields
    resources.seed(world)       # scatter minerals/food/energy
    species.seed(world, n=cfg.sim.start_species, total_pop=cfg.sim.start_population)
    # genesis of nations: open onto a plural world of distinct rival civilizations,
    # not a single people that slowly emerges. (Founding events go to the timeline via
    # the engine's genesis bootstrap.)
    n_civs = int(getattr(cfg.sim, "start_civilizations", 5))
    world.genesis_events = civilization.seed_initial(world, n=n_civs)
    return world


def tick(world: WorldState) -> list[dict]:
    """Advance one deterministic step. Returns any notable events produced this
    tick (for the historical timeline)."""
    world.tick += 1
    new_events: list[dict] = []

    terrain.step(world)                    # erosion, tectonics, volcanism
    climate.step(world)                    # weather under current params
    resources.step(world)                  # regrowth/depletion
    species.step(world)                    # population dynamics, migration
    new_events += evolution.step(world)    # mutations, speciation, extinction
    new_events += civilization.step(world) # civ emergence, diplomacy, war intents
    new_events += cities.step(world)       # city economy, growth, expansion
    new_events += units.step(world)        # spawn + move people; resolve arrivals
    new_events += events.step(world)       # decay active god-mode events
    remember_historical_sites(world, new_events)

    # age out transient world markers
    world.markers = [m for m in world.markers
                     if world.tick - m["born"] < m["ttl"]]
    return new_events


_HISTORICAL_SITE_TYPES = {
    "war": "battlefield",
    "battle": "battlefield",
    "holy_war": "battlefield",
    "revolution": "battlefield",
    "collapse": "ruin",
    "famine": "famine_marker",
    "plague": "plague_marker",
    "settlement": "foundation",
    "religion_founded": "shrine",
    "schism": "schism_site",
    "culture": "monument",
    "economy": "market_crisis",
    "migration": "migration_waypoint",
    "discovery": "discovery_site",
}


def remember_historical_sites(world: WorldState, events_: list[dict]) -> None:
    """Persist material world memory from notable events.

    Sites are render evidence only: ruins, battlefields, shrines, famine scars,
    migration waypoints. They are never invented; if an event cannot be tied to a
    real city/civilization position, it is ignored.
    """
    if not hasattr(world, "historical_sites") or world.historical_sites is None:
        world.historical_sites = []
    if not events_:
        return
    signatures = {s.get("signature") for s in world.historical_sites}
    for ev in events_:
        typ = str(ev.get("type", ""))
        kind = _HISTORICAL_SITE_TYPES.get(typ)
        if not kind:
            continue
        city = _event_city(world, ev)
        if city is None:
            continue
        title = str(ev.get("title", typ))[:96]
        sig = f"{kind}:{city.id}:{typ}:{title}"
        if sig in signatures:
            continue
        signatures.add(sig)
        intensity = _site_intensity(world, city, ev)
        y, x = city.pos
        world.historical_sites.append({
            "id": f"site:{len(world.historical_sites) + 1}:{int(ev.get('tick', world.tick))}",
            "signature": sig,
            "kind": kind,
            "event_type": typ,
            "tick": int(ev.get("tick", world.tick)),
            "city_id": city.id,
            "civ_id": city.civ_id,
            "x": float(x),
            "y": float(y),
            "title": title,
            "detail": str(ev.get("detail", ""))[:180],
            "intensity": round(float(intensity), 3),
        })
    if len(world.historical_sites) > 1500:
        world.historical_sites = world.historical_sites[-1500:]


def _event_city(world: WorldState, ev: dict):
    cid = ev.get("city_id")
    city = world.cities.get(cid) if cid is not None else None
    if city is not None:
        return city
    civ_id = ev.get("civ_id")
    civ = world.civilizations.get(civ_id) if civ_id is not None else None
    if civ is None:
        return None
    live = [c for c in civ.cities(world) if c.alive]
    if not live:
        return None
    return max(live, key=lambda c: c.population)


def _site_intensity(world: WorldState, city, ev: dict) -> float:
    typ = str(ev.get("type", ""))
    if typ in ("collapse", "war", "battle", "holy_war", "revolution"):
        return min(1.0, 0.35 + city.damage * 0.45 + city.unrest * 0.35)
    if typ in ("famine", "plague"):
        return min(1.0, 0.3 + getattr(city, "famine_risk", 0.0) * 0.45 + city.unrest * 0.2)
    if typ in ("religion_founded", "schism", "culture", "discovery"):
        return min(1.0, 0.25 + city.culture / 180 + city.infrastructure / 25)
    if typ == "settlement":
        return min(1.0, 0.28 + city.population / 22000)
    return min(1.0, 0.25 + city.population / 30000)
