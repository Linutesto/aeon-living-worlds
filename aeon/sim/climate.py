"""Climate: temperature, humidity, rainfall, and storms.

A deliberately cheap model: latitude + elevation set a baseline temperature; humidity
advects off water; rainfall follows humidity; storms are stochastic spikes. The
governor's knobs — temperature_bias, rainfall_multiplier, storm_intensity — scale
these fields globally every tick. Real prevailing-wind advection and seasonal cycles
are the depth to add later.
"""

from __future__ import annotations

import numpy as np

from . import world as _w


def initialize(world: "_w.WorldState") -> None:
    h, w = world.height, world.width
    lat = np.abs(np.linspace(-1, 1, h))[:, None] * np.ones((1, w), dtype=np.float32)
    # equator ~32C, poles ~-15C, minus a lapse rate with elevation
    base = 32 - 47 * lat - 25 * np.maximum(world.elevation, 0) + world.params.temperature_bias
    world.temperature = base.astype(np.float32)
    world.humidity = np.clip(0.6 - 0.4 * lat + world.params.humidity_bias, 0, 1).astype(np.float32)
    world.rainfall = np.clip(world.humidity * 0.5 * world.params.rainfall_multiplier, 0, 2).astype(np.float32)
    _w.terrain.classify_biomes(world)


def step(world: "_w.WorldState") -> None:
    p = world.params
    h, w = world.height, world.width
    lat = np.abs(np.linspace(-1, 1, h))[:, None] * np.ones((1, w), dtype=np.float32)
    base = 32 - 47 * lat - 25 * np.maximum(world.elevation, 0) + p.temperature_bias
    # relax toward baseline so events/biases ease in rather than snapping
    world.temperature = (0.9 * world.temperature + 0.1 * base).astype(np.float32)

    # humidity rises near water, falls in heat
    near_water = (world.biome == _w.BIOME["ocean"]) | (world.water > 0.2)
    target_h = np.where(near_water, 0.9, world.humidity * 0.95 + p.humidity_bias * 0.08)
    world.humidity = np.clip(0.8 * world.humidity + 0.2 * target_h, 0, 1).astype(np.float32)

    rain = world.humidity * 0.6 * p.rainfall_multiplier
    if p.storm_intensity > 0 and world.rng.chance("storm", 0.1 * p.storm_intensity):
        rain = rain + _storm(world)
    world.rainfall = np.clip(rain, 0, 2).astype(np.float32)

    _w.terrain.classify_biomes(world)


def _storm(world: "_w.WorldState") -> np.ndarray:
    rng = world.rng.stream("storm")
    h, w = world.height, world.width
    cy, cx = int(rng.integers(0, h)), int(rng.integers(0, w))
    yy, xx = np.mgrid[0:h, 0:w]
    r2 = ((yy - cy) % h) ** 2 + ((xx - cx) % w) ** 2
    return (world.params.storm_intensity * np.exp(-r2 / (2 * 12 ** 2))).astype(np.float32)
