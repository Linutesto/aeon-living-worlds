"""Tests for world-generation config + the restart/reset machinery.

Covers: strict config validation, deterministic restart (same seed ⇒ identical world),
configurable civilization count, layer resets, and the fresh-vs-keep mind behavior.
The Engine is built with the governor + society mind disabled so these run offline/fast.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from aeon.config import load_config
from aeon.engine import Engine
from aeon.sim.worldgen import WorldGenConfig


@pytest.fixture
def cfg():
    c = load_config()
    c = dataclasses.replace(c, governor=dataclasses.replace(c.governor, enabled=False))
    c = dataclasses.replace(c, mind=dataclasses.replace(c.mind, enabled=False))
    c = dataclasses.replace(c, persistence=dataclasses.replace(c.persistence,
                                                               autosave_on_boot=False,
                                                               enabled=False))
    # small + fast worlds for the test
    c = dataclasses.replace(c, world=dataclasses.replace(c.world, width=96, height=96))
    return c


def _world_fingerprint(world) -> tuple:
    """A cheap deterministic signature of the generated world."""
    caps = tuple(sorted((c.pos for c in world.cities.values())))
    return (
        round(float(world.elevation.sum()), 3),
        round(float(world.temperature.sum()), 3),
        len(world.civilizations),
        len(world.cities),
        caps,
    )


# ----------------------------------------------------------- config validation
def test_config_defaults_and_schema():
    g = WorldGenConfig.from_defaults()
    assert g.start_civilizations == 5
    schema = WorldGenConfig.schema()
    keys = {f["key"] for f in schema["structural"]}
    assert {"seed", "width", "height", "start_civilizations"} <= keys
    assert any(f["key"] == "sea_level" for f in schema["params"])
    assert any(f["key"] == "texture_pack" for f in schema["presentation"])


def test_config_rejects_unknown_keys():
    with pytest.raises(ValueError):
        WorldGenConfig.from_dict({"bogus": 1})
    with pytest.raises(ValueError):
        WorldGenConfig.from_dict({"params": {"not_a_knob": 1.0}})
    with pytest.raises(ValueError):
        WorldGenConfig.from_dict({"presentation": {"texture_pack": "nope"}})


def test_config_clamps_numeric_fields():
    g = WorldGenConfig.from_dict({"width": 999999, "start_civilizations": 99,
                                  "params": {"sea_level": 5.0}})
    assert g.width <= 384
    assert g.start_civilizations <= 12
    assert g.params["sea_level"] <= 0.3        # BOUNDS clamp


# ------------------------------------------------------------------ restart
def test_restart_is_deterministic(cfg):
    eng = Engine(cfg)
    g = dataclasses.replace(eng.current_gen_config(), seed=4242, start_civilizations=4)
    eng.restart(g)
    fp1 = _world_fingerprint(eng.world)
    eng.restart(g)                              # same seed again
    fp2 = _world_fingerprint(eng.world)
    assert fp1 == fp2, "same seed must reproduce an identical world"
    assert len(eng.world.civilizations) == 4


def test_restart_different_seed_differs(cfg):
    eng = Engine(cfg)
    eng.restart(dataclasses.replace(eng.current_gen_config(), seed=1))
    a = _world_fingerprint(eng.world)
    eng.restart(dataclasses.replace(eng.current_gen_config(), seed=2))
    b = _world_fingerprint(eng.world)
    assert a != b


def test_restart_configurable_civ_count(cfg):
    eng = Engine(cfg)
    for n in (3, 7):
        eng.restart(dataclasses.replace(eng.current_gen_config(),
                                        seed=99, start_civilizations=n))
        assert len(eng.world.civilizations) == n


def test_restart_fresh_minds_by_default(cfg):
    eng = Engine(cfg)
    brain_before = eng.world.species_brain
    eng.restart(eng.current_gen_config())
    assert eng.world.species_brain is not brain_before     # fresh by default


def test_restart_keep_minds(cfg):
    eng = Engine(cfg)
    brain_before = eng.world.species_brain
    eng.restart(eng.current_gen_config(), keep_minds=True)
    assert eng.world.species_brain is brain_before         # carried over


def test_restart_applies_params_deterministically(cfg):
    eng = Engine(cfg)
    g = dataclasses.replace(eng.current_gen_config(), seed=7)
    g.params["sea_level"] = 0.2                 # higher seas → less land
    eng.restart(g)
    assert eng.world.params.sea_level == pytest.approx(0.2)
    high_sea_land = int(eng.world.land_mask.sum())
    g2 = dataclasses.replace(eng.current_gen_config(), seed=7)
    g2.params["sea_level"] = -0.2               # lower seas → more land
    eng.restart(g2)
    assert int(eng.world.land_mask.sum()) > high_sea_land


# ------------------------------------------------------------------ layers
def test_reset_layer_minds(cfg):
    eng = Engine(cfg)
    brain_before = eng.world.species_brain
    out = eng.reset_layer("minds")
    assert out["reset"] and eng.world.species_brain is not brain_before


def test_reset_layer_civilization_reseeds(cfg):
    eng = Engine(cfg)
    eng.reset_layer("civilization")
    assert len(eng.world.civilizations) == int(cfg.sim.start_civilizations)
    assert all(c.alive for c in eng.world.cities.values())


def test_reset_layer_cities_population_keeps_civs(cfg):
    eng = Engine(cfg)
    civ_ids_before = set(eng.world.civilizations)
    eng.reset_layer("cities_population")
    assert set(eng.world.civilizations) == civ_ids_before    # identities preserved
    assert len(eng.population.people) == 0                   # people wiped


def test_reset_layer_unknown_raises(cfg):
    eng = Engine(cfg)
    with pytest.raises(ValueError):
        eng.reset_layer("nonsense")
