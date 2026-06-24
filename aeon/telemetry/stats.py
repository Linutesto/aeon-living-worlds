"""Compute the world-statistics snapshot.

This is the world's vital-signs panel and the *only* view the governor gets of the
world. Keep it cheap (it runs on the governor clock) and keep the keys stable — both
prompts.py and the dashboard read them by name.
"""

from __future__ import annotations

import math

import numpy as np

from ..sim import species as _sp


def _shannon(populations: list[float]) -> float:
    total = sum(populations)
    if total <= 0:
        return 0.0
    h = 0.0
    for p in populations:
        if p > 0:
            frac = p / total
            h -= frac * math.log(frac)
    # normalize to 0..1 by max possible entropy for this count
    n = sum(1 for p in populations if p > 0)
    return h / math.log(n) if n > 1 else 0.0


def _label(value: float, lo: float, hi: float) -> str:
    if value < lo:
        return "low"
    if value > hi:
        return "high"
    return "moderate"


def compute(world, history, metrics) -> dict:
    alive = [s for s in world.species.values() if s.alive]
    pops = [s.population for s in alive]
    biodiversity = _shannon(pops)
    civs = [c for c in world.civilizations.values() if c.alive]

    land = world.land_mask
    avg_temp = float(world.temperature[land].mean()) if land.any() else 0.0

    # climate stability: inverse of recent avg-temperature variance
    temp_series = metrics.series("avg_temperature")
    climate_var = float(np.var(temp_series[-50:])) if len(temp_series) > 5 else 0.0
    climate_stability = 1.0 / (1.0 + climate_var)

    war_recent = history.count_since(world.tick - 200, type="war")
    war_freq = "high" if war_recent > 5 else "moderate" if war_recent > 1 else "low"

    dominant = max(alive, key=lambda s: s.population, default=None)

    # the human layer — what the observer actually cares about
    live_cities = [c for c in world.cities.values() if c.alive]
    people = world.urban_population
    famines = sum(1 for c in live_cities if c.famine > 0)
    largest = max(live_cities, key=lambda c: c.population, default=None)
    dominant_civ = max(civs, key=lambda c: c.population_of(world), default=None)

    # world health: people vitality + biodiversity + climate, minus unrest/famine
    pop_score = min(1.0, people / 20000.0) if live_cities else min(
        1.0, world.population / max(1, world.cfg.sim.start_population))
    unrest = (sum(c.unrest for c in live_cities) / len(live_cities)) if live_cities else 0.0
    health = 100 * (0.4 * pop_score + 0.3 * biodiversity
                    + 0.2 * climate_stability - 0.3 * unrest)
    health = max(0.0, health)

    from ..sim import season as _season
    return {
        "world_age": world.tick,
        "year": _season.year(world.tick),
        "season": _season.name(world.tick),
        "season_index": _season.index(world.tick),
        "season_progress": round(_season.progress(world.tick), 3),
        "population": int(people),               # people living in cities
        "wildlife": int(world.population),       # total animal/plant population
        "species_count": len(alive),
        "civilization_count": len(civs),
        "city_count": len(live_cities),
        "unit_count": len(world.units),
        "largest_city": (f"{largest.name} ({int(largest.population)})"
                         if largest else "none"),
        "dominant_civ": dominant_civ.name if dominant_civ else "none",
        # a compact roll-call of the living nations and their characters, so the spirit
        # shapes pressures with the plural world in mind (not one faceless civ).
        "nations": "; ".join(
            f"{c.name} ({getattr(c, 'ideology', 'tribal').lower()}, "
            f"{getattr(c, 'diplomatic_stance', 'neutral')}, "
            f"{int(c.population_of(world))})"
            for c in sorted(civs, key=lambda c: -c.population_of(world))[:8]) or "none",
        "famine_count": famines,
        "biodiversity": round(biodiversity, 3),
        "biodiversity_label": _label(biodiversity, 0.35, 0.75),
        "climate_stability": round(climate_stability, 3),
        "climate_stability_label": _label(climate_stability, 0.4, 0.8),
        "avg_temperature": round(avg_temp, 1),
        "war_frequency": war_freq,
        "world_health": round(health, 1),
        "dominant_species": dominant.name if dominant else "none",
        "active_events": [e["kind"] for e in world.active_events],
        "params": world.params.as_dict(),
    }
