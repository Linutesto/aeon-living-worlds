from __future__ import annotations

import math
import time

import numpy as np

from aeon.config import Config
from aeon.sim import terrain
from aeon.sim import world as world_mod
from aeon.sim.params import BOUNDS, WorldParams
from aeon.sim.worldgen import WorldGenConfig


def _cfg(seed: int, size: int = 72, civs: int = 5) -> Config:
    cfg = Config()
    cfg.world.seed = seed
    cfg.world.width = size
    cfg.world.height = size
    cfg.sim.start_civilizations = civs
    cfg.sim.start_species = 3
    cfg.sim.start_population = 1800
    cfg.persistence.enabled = False
    return cfg


def test_worldgen_knobs_are_exposed_to_setup_schema():
    schema = WorldGenConfig.schema()
    params = {p["key"] for p in schema["params"]}
    for key in (
        "land_percent", "mountain_percent", "river_count", "forest_density",
        "desert_frequency", "snow_line", "city_density", "min_city_distance",
        "road_importance", "building_density", "district_size",
    ):
        assert key in BOUNDS
        assert key in params


def test_candidate_world_has_finite_buildability_and_valid_report():
    w = world_mod.create_world(_cfg(1337, size=96))
    assert w.generation_report["valid"] is True
    assert 0.35 <= w.generation_report["land_ratio"] <= 0.80
    assert w.generation_report["good_buildable_tiles"] > 100
    for layer in (w.terrain_slope, w.water_distance, w.mountain_distance,
                  w.buildable_score, w.road_access):
        assert layer.shape == (w.height, w.width)
        assert np.isfinite(layer).all()


def test_city_founders_use_high_quality_buildable_regions():
    w = world_mod.create_world(_cfg(7, size=96))
    assert len(w.cities) == 5
    for city in w.cities.values():
        y, x = city.pos
        assert w.land_mask[y, x]
        assert w.buildable_score[y, x] >= 0.35
        assert terrain.validate_terrain(w)[0]


def test_cities_never_overlap_minimum_spacing():
    p = WorldParams.from_defaults()
    p.min_city_distance = 18
    w = world_mod.create_world(_cfg(11, size=96), params=p)
    cities = list(w.cities.values())
    for i, a in enumerate(cities):
        for b in cities[i + 1:]:
            d = abs(a.pos[0] - b.pos[0]) + abs(a.pos[1] - b.pos[1])
            assert d >= p.min_city_distance


def test_roads_are_first_class_and_avoid_blocked_terrain():
    w = world_mod.create_world(_cfg(17, size=96))
    assert w.road_graph
    assert float(w.road_access.max()) > 0.0
    for road in w.road_graph:
        assert len(road["points"]) >= 2
        for y, x in road["points"]:
            assert w.land_mask[y, x]
            assert w.terrain_slope[y, x] < 0.35
            assert w.water[y, x] <= 0.55


def test_generation_batch_rejects_broken_topologies_quickly():
    start = time.perf_counter()
    ratios: list[float] = []
    attempts: list[int] = []
    for seed in range(60):
        w = world_mod.create_world(_cfg(seed, size=64, civs=4))
        report = w.generation_report
        assert report["valid"] is True
        assert report["reasons"] == []
        assert len(w.cities) == 4
        assert w.road_graph
        ratios.append(report["land_ratio"])
        attempts.append(report["attempts"])
    elapsed = time.perf_counter() - start
    assert 0.42 <= float(np.mean(ratios)) <= 0.72
    assert max(attempts) <= 24
    assert elapsed < 30.0


def test_generation_has_no_nan_model_or_render_inputs():
    w = world_mod.create_world(_cfg(23, size=96))
    numeric = [
        w.elevation, w.water, w.temperature, w.humidity, w.rainfall,
        w.food, w.minerals, w.energy, w.buildable_score, w.road_access,
    ]
    for layer in numeric:
        assert np.isfinite(layer).all()
    for city in w.cities.values():
        assert math.isfinite(city.road_access)
        assert city.building_spacing > 0
        assert city.district_size > 0
