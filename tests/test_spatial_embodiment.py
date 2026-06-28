from __future__ import annotations

import math

import numpy as np

from aeon.agents import spatial
from aeon.mind import encode as enc


def _resident(engine):
    city = max((c for c in engine.world.cities.values() if c.alive),
               key=lambda c: c.population)
    engine.population.focus(engine.world, city.id)
    return city, engine.population.residents(city.id)[0]


def test_spatial_feature_extraction_no_nan(grown_engine):
    city, p = _resident(grown_engine)
    obs = spatial.compact_observation(
        grown_engine.world, grown_engine.population, p, city,
        grown_engine.population.spatial_index)
    feats = grown_engine.population.features(p, city, grown_engine.world)
    assert len(feats) == enc.N_FEAT
    assert np.isfinite(np.asarray(feats, dtype=float)).all()
    assert obs["nearest_city"] is not None
    assert "safety_score" in obs["features"]


def test_citizen_target_selection_is_structured(grown_engine):
    city, p = _resident(grown_engine)
    action = spatial.choose_target(
        grown_engine.world, grown_engine.population, p, "work", city,
        grown_engine.population.spatial_index)
    assert action["type"] == "work"
    assert action["target_kind"] in enc.TARGET_KINDS
    assert len(action["target_position"]) == 3
    assert action["reason"]
    # embodied movement intent rides every target (focus area 5)
    assert action["movement_intent"] in spatial.MOVEMENT_INTENTS
    assert action["movement_intent"] == "go_work"


def test_new_influence_fields_in_feature_vector(grown_engine):
    city, p = _resident(grown_engine)
    obs = spatial.observation(grown_engine.world, grown_engine.population, p, city,
                              grown_engine.population.spatial_index)
    for k in ("war_front_proximity", "famine_zone_proximity", "religion_influence",
              "market_proximity", "temple_proximity", "migration_path_score"):
        assert k in obs["features"]
        assert 0.0 <= obs["features"][k] <= 1.0
    vec = spatial.spatial_feature_vector(grown_engine.world, grown_engine.population, p, city)
    assert len(vec) == len(spatial.SPATIAL_FEATURES)


def test_pathfinding_avoids_blocked_water(grown_engine):
    w = grown_engine.world
    land = [(y, x) for y in range(w.height) for x in range(w.width)
            if w.land_mask[y, x] and int(w.biome[y, x]) != spatial.OCEAN]
    start = land[0]
    goal = land[-1]
    path, ok = spatial.pathfind(w, start, goal, max_nodes=400)
    assert path
    assert ok or len(path) > 1
    assert all(spatial.passable(w, y, x) for y, x in path)


def test_movement_updates_position(grown_engine):
    city, p = _resident(grown_engine)
    start = spatial.current_tile(p)
    target = spatial.choose_target(
        grown_engine.world, grown_engine.population, p, "visit_city_center", city,
        grown_engine.population.spatial_index)
    spatial.begin_movement(grown_engine.world, p, target, grown_engine.population.spatial_counters)
    before = spatial.current_tile(p)
    for _ in range(4):
        spatial.advance_movement(grown_engine.world, p, speed=1.25)
    after = spatial.current_tile(p)
    assert p.path
    assert before != after or after == tuple(map(int, p.destination or start))
    assert math.isfinite(p.position[0]) and math.isfinite(p.position[1])


def test_spatial_debug_counters(grown_engine):
    dbg = grown_engine.spatial_debug()
    assert dbg["positioned"] > 0
    assert "avg_path_length" in dbg
    assert "feature_count" in dbg and dbg["feature_count"] > 0
