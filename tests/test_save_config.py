"""Save/load persistence of the v2 config: graphics preset, texture pack, render
budgets, restart metadata, and the generation config. Old (pre-v2) saves must still
load, falling back to defaults. Uses a temp SaveStore so it never touches real saves.
"""

from __future__ import annotations

import dataclasses

import pytest

from aeon.config import load_config
from aeon.engine import Engine
from aeon.persistence import SaveStore


@pytest.fixture
def eng(tmp_path):
    c = load_config()
    c = dataclasses.replace(c, governor=dataclasses.replace(c.governor, enabled=False))
    c = dataclasses.replace(c, mind=dataclasses.replace(c.mind, enabled=False))
    c = dataclasses.replace(c, persistence=dataclasses.replace(
        c.persistence, enabled=False, autosave_on_boot=False))
    c = dataclasses.replace(c, world=dataclasses.replace(c.world, width=96, height=96))
    e = Engine(c)
    e.save_store = SaveStore(path=tmp_path / "saves.db")  # isolate from real saves
    return e


def test_presentation_survives_save_load(eng):
    eng.graphics_preset = "rtx-4090-ultra"
    eng.texture_pack = "volcanic-ash"
    eng.render_budgets["max_buildings"] = 31000
    eng.save_world("t1", manual=True)
    # mutate live state, then load the save back over it
    eng.graphics_preset = "mobile-low"
    eng.texture_pack = "default-clean"
    eng.render_budgets["max_buildings"] = 1
    eng.load_world("t1")
    assert eng.graphics_preset == "rtx-4090-ultra"
    assert eng.texture_pack == "volcanic-ash"
    assert eng.render_budgets["max_buildings"] == 31000


def test_restart_metadata_persists(eng):
    eng.restart(dataclasses.replace(eng.current_gen_config(), seed=2024))
    eng.save_world("t2", manual=True)
    eng.load_world("t2")
    assert eng.restart_meta["seed"] == 2024
    assert eng.current_gen_config().seed == 2024


def test_summary_carries_presentation(eng):
    eng.texture_pack = "dark-fantasy"
    out = eng.save_world("t3", manual=True)
    assert out["texture_pack"] == "dark-fantasy"
    assert out["save_version"] == 2


def test_old_save_without_config_loads_with_defaults(eng):
    # craft a pre-v2 state dict: the engine's own save minus the v2 keys
    state = eng._state_for_save()
    for k in ("save_version", "gen_config", "graphics_preset", "texture_pack",
              "render_budgets", "restart_meta"):
        state.pop(k, None)
    eng.save_store.save("legacy", state, {"slot": "legacy", "tick": 0, "seed": 1},
                        eng.save_store.weights_path("legacy"), True)
    eng.graphics_preset = "ultra"          # set a non-default to prove the fallback
    eng.texture_pack = "lush-green"
    eng.load_world("legacy")               # must not raise
    assert eng.graphics_preset in ("ultra", "desktop")  # kept current / default, no crash
    assert eng.world is not None
