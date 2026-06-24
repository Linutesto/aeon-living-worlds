"""Tests for the seasonal world (Phase 12) and a long-run civilization smoke test
(Phase 15) — the world must keep telling stories over centuries."""

from __future__ import annotations

from aeon.config import load_config
from aeon.engine import Engine
from aeon.sim import season as S
from aeon.sim import world as W


def test_season_cycle_is_deterministic_and_complete():
    seen = [S.name(t) for t in range(0, S.TICKS_PER_YEAR, S.TICKS_PER_YEAR // 4)]
    assert seen == ["Spring", "Summer", "Autumn", "Winter"]
    assert S.year(0) == 0 and S.year(S.TICKS_PER_YEAR) == 1
    # winter is lean, summer is bountiful — the season genuinely moves the economy
    assert S.food_factor(S.TICKS_PER_YEAR * 3 // 4) < S.food_factor(S.TICKS_PER_YEAR // 4)


def test_stats_expose_season():
    cfg = load_config()
    cfg.governor.enabled = False
    cfg.persistence.enabled = False
    cfg.persistence.autosave_on_boot = False
    e = Engine(cfg)
    for _ in range(400):
        W.tick(e.world)
    s = e.snapshot_stats()
    assert s["season"] in S.NAMES
    assert 0.0 <= s["season_progress"] <= 1.0
    assert s["season_index"] == S.index(e.world.tick)


def test_long_run_world_tells_stories():
    """Phase 15: run a world for a long span and verify history actually accumulates —
    cities rise, faiths/factions emerge, and events pile up without crashing."""
    cfg = load_config()
    cfg.governor.enabled = False
    cfg.persistence.enabled = False
    cfg.persistence.autosave_on_boot = False
    cfg.world.seed = 7
    e = Engine(cfg)
    # ~2 in-world years of sim + society (collect events as the real engine loop does)
    for _ in range(2500):
        e.history.extend(W.tick(e.world))
    live = [c for c in e.world.cities.values() if c.alive]
    for c in sorted(live, key=lambda c: -c.population)[:6]:
        e.population.focus(e.world, c.id)
    for _ in range(300):
        e.world.tick += 1
        e.population.tick(e.world)
        if e.population._last_life_tick == e.world.tick:
            soc = e.society.step(e.world, e.population)
            e.history.extend(soc)
    assert len([c for c in e.world.cities.values() if c.alive]) > 0   # cities exist
    assert len(e.history.recent(2000)) > 20                           # history accrued
    assert e.discoveries()["discoveries"]                              # records emerge
    # the world survived multiple seasons turning
    assert S.year(e.world.tick) >= 2
