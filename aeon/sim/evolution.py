"""Evolution: mutation under pressure, speciation, and extinction bookkeeping.

Each tick, thriving species may throw off a mutant daughter species; the mutation
rate is the governor's `mutation_rate` knob amplified by environmental stress (a
species living far from its thermal optimum mutates faster). Extinctions detected in
species.step() are turned into timeline events here.

Returns a list of event dicts for the historical timeline.
"""

from __future__ import annotations

import numpy as np

from . import world as _w
from . import species as _sp


def _stress(world, sp) -> float:
    y, x = int(sp.pos[0]) % world.height, int(sp.pos[1]) % world.width
    return float(abs(world.temperature[y, x] - sp.genome["heat_tolerance"]) / 40.0)


# soft cap on living species; above this, speciation is suppressed to keep the
# world legible (and the dashboard payloads bounded).
SPECIES_SOFT_CAP = 80


def step(world: "_w.WorldState") -> list[dict]:
    p = world.params
    rng = world.rng.stream("evolution")
    out: list[dict] = []
    n_alive = sum(1 for s in world.species.values() if s.alive)
    crowding = max(0.0, 1.0 - n_alive / SPECIES_SOFT_CAP)  # 0 when at/over cap

    for sp in list(world.species.values()):
        # record extinctions flagged by population collapse
        if not sp.alive and sp.extinct_tick == world.tick:
            out.append({
                "tick": world.tick, "type": "extinction",
                "title": f"{sp.name} went extinct",
                "detail": f"The {sp.diet} lineage {sp.name} died out after "
                          f"{world.tick - sp.born_tick} ticks.",
                "species_id": sp.id,
            })
            continue
        if not sp.alive:
            continue

        rate = p.mutation_rate * (1.0 + 2.0 * _stress(world, sp)) * crowding
        if sp.population > 50 and rng.random() < rate:
            child = _mutate(world, sp, rng)
            out.append({
                "tick": world.tick, "type": "speciation",
                "title": f"{child.name} diverged from {sp.name}",
                "detail": f"A mutant {child.diet} lineage split off under "
                          f"environmental pressure.",
                "species_id": child.id, "ancestor_id": sp.id,
            })
    return out


def _mutate(world, parent, rng):
    genome = dict(parent.genome)
    for k in genome:
        genome[k] = float(genome[k] * (1 + rng.normal(0, 0.15)))
    genome["heat_tolerance"] = float(np.clip(genome["heat_tolerance"], -20, 50))
    # rare diet shift — how predators are born
    diet = parent.diet
    if rng.random() < 0.1:
        diet = rng.choice([_sp.PLANT, _sp.HERBIVORE, _sp.PREDATOR])
    child = _sp.spawn(
        world, diet=diet, pos=parent.pos,
        population=parent.population * 0.2, genome=genome, ancestor_id=parent.id,
    )
    parent.population *= 0.8
    child.history.append(f"Diverged from {parent.name} at tick {world.tick}.")
    return child
