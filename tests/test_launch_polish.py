"""Tests for the launch-polish pass: dead interviews, pagination, time controls."""

from __future__ import annotations

import asyncio
import json

import pytest

from aeon.server.encoding import to_jsonable


def _alive_person(engine):
    live = [c for c in engine.world.cities.values() if c.alive]
    cid = max(live, key=lambda c: c.population).id
    engine.population.focus(engine.world, cid)
    res = engine.population.residents(cid)
    assert res
    return res[0]


def _kill(p, tick):
    p.alive = False
    p.death_tick = tick
    p.death_cause = "fever"


# ---------------- Priority 1: dead interviews ----------------

def test_dead_person_cannot_live_interview(grown_engine):
    p = _alive_person(grown_engine)
    _kill(p, grown_engine.world.tick)
    result = asyncio.run(grown_engine.interview_person(p.id, "Who are you?"))
    assert result["deceased"] is True
    assert "archive" in result
    # the answer must NOT be live dialogue — it is the spirit-disabled message
    assert "silence" in result["answer"].lower() or "dead" in result["answer"].lower()


def test_dead_person_exposes_archive_in_inspect(grown_engine):
    p = _alive_person(grown_engine)
    p.milestones.append("Did a notable thing.")
    _kill(p, grown_engine.world.tick)
    dossier = grown_engine.inspect_person(p.id)
    assert dossier["alive"] is False
    arch = dossier["archive"]
    assert arch and arch["deceased"] is True
    assert "biography" in arch and "legacy" in arch
    json.dumps(to_jsonable(dossier), allow_nan=False)


def test_alive_person_has_no_archive(grown_engine):
    p = _alive_person(grown_engine)
    assert p.alive
    dossier = grown_engine.inspect_person(p.id)
    assert dossier["archive"] is None


# ---------------- Priority 3: pagination & search ----------------

def test_people_directory_paginates(grown_engine):
    cid = max((c for c in grown_engine.world.cities.values() if c.alive),
              key=lambda c: c.population).id
    grown_engine.population.focus(grown_engine.world, cid)
    page1 = grown_engine.people_directory(city_id=cid, limit=5, offset=0)
    assert len(page1["people"]) <= 5
    assert "count" in page1 and "has_more" in page1 and "offset" in page1
    if page1["count"] > 5:
        page2 = grown_engine.people_directory(city_id=cid, limit=5, offset=5)
        ids1 = {p["id"] for p in page1["people"]}
        ids2 = {p["id"] for p in page2["people"]}
        assert ids1.isdisjoint(ids2)               # no overlap across pages


def test_people_directory_limit_is_capped(grown_engine):
    # a malicious huge limit must be clamped, never returning an unbounded list
    res = grown_engine.people_directory(limit=999999)
    assert res["limit"] <= 200


def test_people_alive_filter(grown_engine):
    p = _alive_person(grown_engine)
    cid = p.home_city
    _kill(p, grown_engine.world.tick)
    dead = grown_engine.people_directory(city_id=cid, alive=False, limit=50)
    assert any(item["id"] == p.id for item in dead["people"])
    alive = grown_engine.people_directory(city_id=cid, alive=True, limit=50)
    assert all(item["id"] != p.id for item in alive["people"])


def test_cities_directory_paginates_and_searches(grown_engine):
    res = grown_engine.cities_directory(limit=5, offset=0)
    assert len(res["cities"]) <= 5 and "has_more" in res
    # search by a real city's name returns it
    name = res["cities"][0]["name"]
    found = grown_engine.cities_directory(q=name[:4], limit=10)
    assert any(name[:4].lower() in c["name"].lower() for c in found["cities"])


def test_buildings_directory_and_inspect(grown_engine):
    live = [c for c in grown_engine.world.cities.values() if c.alive]
    cid = next((c.id for c in live if getattr(c, "building_entities", {})), None)
    if cid is None:
        pytest.skip("no building entities materialized in this world")
    res = grown_engine.buildings_directory(city_id=cid, limit=5)
    assert "buildings" in res and len(res["buildings"]) <= 5
    if res["buildings"]:
        bid = res["buildings"][0]["id"]
        rec = grown_engine.inspect_building(bid)
        assert rec is not None
        json.dumps(to_jsonable(rec), allow_nan=False)


# ---------------- Priority 2: time controls ----------------

def test_sub_unit_speed_runs_slower_than_x1():
    """x0.25 must advance fewer ticks than x1 over the same wall time — the bug was
    int(0.25)==0 collapsing slow modes to x1."""
    from aeon.config import load_config
    from aeon.engine import Engine

    def run_at(speed, wakes):
        cfg = load_config()
        cfg.governor.enabled = False
        cfg.persistence.enabled = False
        cfg.persistence.autosave_on_boot = False
        e = Engine(cfg)
        # mimic the accumulator loop deterministically (no real sleeping)
        interval = 1.0 / cfg.sim.loop_hz
        acc = 0.0
        start = e.world.tick
        for _ in range(wakes):
            acc += speed * cfg.sim.base_tps * interval
            steps = int(acc)
            acc -= steps
            steps = min(steps, cfg.sim.max_steps_per_wake)
            for _ in range(steps):
                from aeon.sim import world as W
                W.tick(e.world)
        return e.world.tick - start

    slow = run_at(0.25, 200)
    normal = run_at(1.0, 200)
    fast = run_at(10.0, 200)
    assert 0 < slow < normal < fast
    # x0.25 should be roughly a quarter of x1
    assert slow == pytest.approx(normal * 0.25, rel=0.2)


def test_pause_sets_speed_zero(fresh_engine):
    fresh_engine.set_speed(5)
    assert fresh_engine.speed == 5
    fresh_engine.pause()
    assert fresh_engine.speed == 0
