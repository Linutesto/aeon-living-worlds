"""Resources: minerals (finite), food (renewable), energy (sources).

Food regrows toward a biome-dependent ceiling scaled by plant_growth and rainfall;
species consumption depletes it in species.py. Minerals are seeded once and only
deplete (civ mining, later). Energy marks special tiles (geothermal near volcanoes,
etc.). resource_richness scales the seeded abundance.
"""

from __future__ import annotations

import numpy as np

from . import world as _w


# per-biome food carrying ceiling (0..1)
_FOOD_CEIL = {
    _w.BIOME["ocean"]: 0.3, _w.BIOME["beach"]: 0.2, _w.BIOME["grassland"]: 0.8,
    _w.BIOME["forest"]: 1.0, _w.BIOME["desert"]: 0.1, _w.BIOME["mountain"]: 0.2,
    _w.BIOME["snow"]: 0.05, _w.BIOME["swamp"]: 0.7, _w.BIOME["tundra"]: 0.15,
}


def seed(world: "_w.WorldState") -> None:
    rng = world.rng.stream("resources")
    h, w = world.height, world.width
    r = world.params.resource_richness
    world.minerals = (rng.random((h, w)).astype(np.float32) ** 3 * r)
    world.energy = (rng.random((h, w)).astype(np.float32) ** 6 * r)  # rare hotspots
    world.food = _food_ceiling(world) * 0.5


def _food_ceiling(world: "_w.WorldState") -> np.ndarray:
    ceil = np.zeros((world.height, world.width), dtype=np.float32)
    for bid, c in _FOOD_CEIL.items():
        ceil[world.biome == bid] = c
    return ceil


def step(world: "_w.WorldState") -> None:
    p = world.params
    ceil = _food_ceiling(world) * p.carrying_capacity
    # logistic regrowth modulated by rainfall and the plant_growth knob
    growth = 0.05 * p.plant_growth * (0.5 + world.rainfall)
    world.food = np.clip(
        world.food + growth * (ceil - world.food), 0, None
    ).astype(np.float32)
