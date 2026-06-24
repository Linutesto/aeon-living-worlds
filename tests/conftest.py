"""Shared pytest fixtures for AEON.

The expensive fixture is a *grown* world: one with cities, materialized individuals,
and an active society (religions/factions). It's session-scoped and treated as
read-only by the serialization tests so we pay the tick cost once.
"""

from __future__ import annotations

import pytest

from aeon.config import load_config
from aeon.engine import Engine
from aeon.sim import world as world_mod


def _grow(engine: Engine, sim_ticks: int = 1100, life_ticks: int = 60) -> None:
    """Tick the world until cities exist, then materialize people and run society."""
    for _ in range(sim_ticks):
        world_mod.tick(engine.world)
    live = sorted((c for c in engine.world.cities.values() if c.alive),
                  key=lambda c: -c.population)
    for c in live[:6]:
        engine.population.focus(engine.world, c.id)
    for _ in range(life_ticks):
        engine.world.tick += 1
        engine.population.tick(engine.world)
        if engine.population._last_life_tick == engine.world.tick:
            engine.society.step(engine.world, engine.population)


@pytest.fixture(scope="session")
def grown_engine():
    """A populated world with cities, individuals, and society. Read-only — do not
    mutate across tests."""
    engine = _make_engine()
    _grow(engine)
    return engine


@pytest.fixture
def fresh_engine():
    """A brand-new world (genesis only) for cheap structural tests."""
    return _make_engine()


def _make_engine() -> Engine:
    cfg = load_config()
    cfg.governor.enabled = False          # no Ollama needed for tests
    cfg.world.seed = 7                     # deterministic
    # disable persistence so construction doesn't load (or overwrite) the real
    # autosave and clobber the deterministic test world.
    cfg.persistence.enabled = False
    cfg.persistence.autosave_on_boot = False
    # keep tests hermetic: the Society Intelligence Stack writes a real dataset and
    # reads cross-project trace files. Tests that need it build it explicitly with a
    # tmp_path (see test_mind.py); construction here stays off.
    cfg.mind.enabled = False
    return Engine(cfg)
