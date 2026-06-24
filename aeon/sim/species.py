"""Species: creatures, plants, and predators as population-dynamics agents.

A species is *not* a per-individual agent here — that's far too heavy for a world
ticking many times a second. Instead each species carries a genome (numeric traits),
a population, a habitat preference, and a position cloud (centroid + spread). Each
tick we resolve births/deaths from food, predation, and climate fit, then drift the
centroid toward better habitat (migration). Per-individual agents are a later
upgrade if a region needs the detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import world as _w

# diet / trophic roles
PLANT, HERBIVORE, PREDATOR = "plant", "herbivore", "predator"

_NAME_PARTS_A = ["thrum", "vesh", "korr", "lume", "drak", "syl", "umbra", "fen", "qoth", "azel"]
_NAME_PARTS_B = ["id", "ax", "or", "een", "ula", "ix", "oth", "ar", "yx", "im"]


@dataclass
class Species:
    id: int
    name: str
    diet: str
    population: float
    genome: dict[str, float]          # trait -> value (heat_tol, speed, size, ...)
    pos: tuple[float, float]          # centroid (y, x) in grid coords
    spread: float                     # rough radius of the population cloud
    born_tick: int
    ancestor_id: int | None = None
    extinct_tick: int | None = None
    history: list[str] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return self.extinct_tick is None and self.population >= 1


def _random_genome(rng) -> dict[str, float]:
    return {
        "heat_tolerance": float(rng.uniform(-10, 40)),   # ideal temperature
        "size": float(rng.uniform(0.1, 5.0)),
        "speed": float(rng.uniform(0.1, 1.0)),
        "aggression": float(rng.uniform(0.0, 1.0)),
        "fertility": float(rng.uniform(0.5, 1.5)),
    }


def _name(rng) -> str:
    a = _NAME_PARTS_A[int(rng.integers(0, len(_NAME_PARTS_A)))]
    b = _NAME_PARTS_B[int(rng.integers(0, len(_NAME_PARTS_B)))]
    return (a + b).capitalize()


def seed(world: "_w.WorldState", n: int, total_pop: int) -> None:
    rng = world.rng.stream("species")
    land = np.argwhere(world.land_mask)
    for i in range(n):
        y, x = land[int(rng.integers(0, len(land)))]
        diet = [PLANT, HERBIVORE, PREDATOR][i % 3]
        spawn(world, diet=diet, pos=(float(y), float(x)),
              population=total_pop / n, genome=_random_genome(rng))


def spawn(world, diet, pos, population, genome, ancestor_id=None, name=None) -> "Species":
    sid = world.new_species_id()
    sp = Species(
        id=sid,
        name=name or _name(world.rng.stream("species")),
        diet=diet,
        population=float(population),
        genome=genome,
        pos=pos,
        spread=6.0,
        born_tick=world.tick,
        ancestor_id=ancestor_id,
    )
    world.species[sid] = sp
    return sp


def _habitat_score(world, sp: "Species") -> float:
    """How well the species' centroid tile suits it right now (0..1-ish)."""
    y, x = int(sp.pos[0]) % world.height, int(sp.pos[1]) % world.width
    if not world.land_mask[y, x] and sp.diet != PLANT:
        return 0.1
    temp_fit = np.exp(-((world.temperature[y, x] - sp.genome["heat_tolerance"]) ** 2) / 200)
    food = float(world.food[y, x])
    return float(0.5 * temp_fit + 0.5 * food)


# a single land tile supports roughly this many individuals at capacity 1.0
TILE_CAPACITY = 1500.0


def world_capacity(world: "_w.WorldState") -> float:
    land_tiles = int(world.land_mask.sum())
    return max(1.0, land_tiles * TILE_CAPACITY * world.params.carrying_capacity)


def step(world: "_w.WorldState") -> None:
    p = world.params
    total_pred = sum(s.population for s in world.species.values()
                     if s.alive and s.diet == PREDATOR)
    total_prey = sum(s.population for s in world.species.values()
                     if s.alive and s.diet in (PLANT, HERBIVORE))
    # global density brake: as the world fills toward capacity, growth -> 0
    crowd = min(1.0, world.population / world_capacity(world))
    density = max(0.0, 1.0 - crowd)   # brake only: never inverts growth sign

    for sp in list(world.species.values()):
        if not sp.alive:
            continue
        score = _habitat_score(world, sp)
        fert = sp.genome["fertility"] * (
            p.predator_fertility if sp.diet == PREDATOR else p.prey_fertility
        )
        # logistic-ish growth from habitat fit, damped by global crowding
        growth = 0.08 * fert * (score - 0.5) * density
        # predation pressure / starvation
        if sp.diet == PREDATOR:
            growth -= 0.05 if total_prey < total_pred * 3 else 0.0
        else:
            growth -= 0.03 * (total_pred / max(total_prey, 1.0))
        sp.population = max(0.0, sp.population * (1 + growth))

        _migrate(world, sp, score)

        if sp.population < 1:
            sp.population = 0
            sp.extinct_tick = world.tick  # evolution.step records the event


def _migrate(world, sp: "Species", score: float) -> None:
    """Drift centroid toward a better-scoring neighbor (gradient ascent)."""
    if score > 0.7:
        return
    rng = world.rng.stream("migrate")
    best, by, bx = score, sp.pos[0], sp.pos[1]
    for _ in range(4):
        ny = sp.pos[0] + rng.uniform(-3, 3) * sp.genome["speed"]
        nx = sp.pos[1] + rng.uniform(-3, 3) * sp.genome["speed"]
        probe = Species(sp.id, sp.name, sp.diet, sp.population, sp.genome,
                        (ny, nx), sp.spread, sp.born_tick)
        s = _habitat_score(world, probe)
        if s > best:
            best, by, bx = s, ny, nx
    sp.pos = (by % world.height, bx % world.width)
