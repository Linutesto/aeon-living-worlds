"""Tests for daily schedules (Phase 1), family + inheritance (Phase 2), and the
season→migration link (Phase 5)."""

from __future__ import annotations

import json

from aeon.agents import schedule as sched
from aeon.sim import season as S
from aeon.sim import cities as city_mod
from aeon.sim import civilization as civ_mod
from aeon.sim import world as world_mod
from aeon.server.encoding import to_jsonable


def _resident(engine):
    c = max((c for c in engine.world.cities.values() if c.alive),
            key=lambda c: c.population)
    engine.population.focus(engine.world, c.id)
    return engine.population.residents(c.id)[0]


# ---------------- Phase 1: daily schedules ----------------

def test_schedule_changes_over_the_day(grown_engine):
    p = _resident(grown_engine)
    base = grown_engine.world.tick - (grown_engine.world.tick % sched.TICKS_PER_DAY)
    acts = []
    for h in range(0, 24, 3):
        grown_engine.world.tick = base + h
        s = sched.schedule(p, grown_engine.world)
        assert s["hour"] == h
        acts.append(s["activity"])
    assert len(set(acts)) > 1, "a day should contain more than one activity"
    # a normal day includes sleep at night
    grown_engine.world.tick = base + 2
    assert sched.schedule(p, grown_engine.world)["activity"] == "sleep"


def test_profession_routines_differ():
    # priests pray/serve; soldiers patrol/train — the routines must not be identical
    assert sched._blocks("priest") != sched._blocks("soldier")
    assert sched._blocks("farmer") != sched._blocks("noble")


def test_person_live_includes_schedule(grown_engine):
    p = _resident(grown_engine)
    live = grown_engine.person_live(p.id)
    for key in ("hour", "time_of_day", "activity", "next_activity", "why", "season"):
        assert key in live
    json.dumps(to_jsonable(live), allow_nan=False)


# ---------------- Phase 2: family + inheritance ----------------

def test_family_tree_structure(grown_engine):
    p = _resident(grown_engine)
    ft = grown_engine.family_tree(p.id)
    assert ft and ft["id"] == p.id and "dynasty" in ft
    for key in ("parents", "siblings", "children"):
        assert isinstance(ft[key], list)
    json.dumps(to_jsonable(ft), allow_nan=False)


def test_inheritance_transfers_wealth(grown_engine):
    e = grown_engine
    # find a married couple, both alive
    couple = next(((q, e.population.get(q.partner_id)) for q in e.population.people.values()
                   if q.alive and q.partner_id and e.population.get(q.partner_id)
                   and e.population.get(q.partner_id).alive), None)
    if couple is None:
        return
    dec, spouse = couple
    dec.wealth = 40.0
    before = spouse.wealth
    e.population._die(e.world, dec, e.world.cities.get(dec.home_city))
    assert spouse.wealth > before                 # estate passed to the spouse
    assert dec.wealth == 0.0
    assert any("Inherited" in m for m in spouse.milestones)


# ---------------- Phase 5: seasons affect travel/migration ----------------

def test_season_travel_factor():
    winter = S.travel_factor(S.TICKS_PER_YEAR * 3 // 4)
    summer = S.travel_factor(S.TICKS_PER_YEAR // 4)
    assert winter < summer                        # winter roads slow migration
    assert S.vegetation_factor(S.TICKS_PER_YEAR * 3 // 4) < S.vegetation_factor(S.TICKS_PER_YEAR // 4)


# ---------------- Resource economy ----------------

def test_city_resource_update_deterministic(fresh_engine):
    e = fresh_engine
    for _ in range(900):
        world_mod.tick(e.world)
    city = max((c for c in e.world.cities.values() if c.alive),
               key=lambda c: c.population)
    before = dict(city.stocks)
    world_mod.tick(e.world)
    after = dict(city.stocks)
    assert before != after
    for name in ("resource_production", "resource_consumption", "shortages", "surplus"):
        data = getattr(city, name)
        assert set(data) >= {"food", "wood", "stone", "metal", "energy", "labor", "luxury", "knowledge"}
    assert 0 <= city.demand_pressure <= 1
    assert 0 <= city.trade_dependency <= 1
    assert 0 <= city.war_readiness <= 1
    assert 0 <= city.civic_stability <= 1
    for name in ("demographics", "class_mix", "professions"):
        data = getattr(city, name)
        assert data
        assert 0.99 <= sum(data.values()) <= 1.01
    assert 0 <= city.education <= 1
    assert 0 <= city.urbanization <= 1
    assert 0 <= city.migration_pressure <= 1
    assert city.fertility_rate >= 0
    assert city.mortality_rate >= 0


def test_shortages_affect_city_pressure(fresh_engine):
    e = fresh_engine
    for _ in range(900):
        world_mod.tick(e.world)
    city = max((c for c in e.world.cities.values() if c.alive),
               key=lambda c: c.population)
    city.stocks.update({k: 0.0 for k in ("food", "wood", "stone", "metal", "energy", "luxury", "knowledge")})
    city.unrest = 0.15
    before_unrest = city.unrest
    world_mod.tick(e.world)
    assert city.shortages["food"] > 0
    assert city.famine_risk > 0
    assert city.demand_pressure > 0
    assert city.migration_pressure > 0
    assert city.unrest >= before_unrest


def test_historical_sites_are_persistent_world_memory(fresh_engine):
    e = fresh_engine
    for _ in range(900):
        world_mod.tick(e.world)
    city = max((c for c in e.world.cities.values() if c.alive),
               key=lambda c: c.population)
    ev = {"tick": e.world.tick, "type": "war", "city_id": city.id,
          "title": f"Battle at {city.name}", "detail": "A real test battle."}
    world_mod.remember_historical_sites(e.world, [ev])
    world_mod.remember_historical_sites(e.world, [ev])
    sites = [s for s in e.world.historical_sites
             if s["city_id"] == city.id and s["event_type"] == "war"
             and s["title"] == f"Battle at {city.name}"]
    assert len(sites) == 1
    assert sites[0]["kind"] == "battlefield"
    assert sites[0]["x"] == city.pos[1]
    assert sites[0]["y"] == city.pos[0]


def test_historical_sites_feed_back_into_city_memory(fresh_engine):
    e = fresh_engine
    for _ in range(900):
        world_mod.tick(e.world)
    city = max((c for c in e.world.cities.values() if c.alive),
               key=lambda c: c.population)
    world_mod.remember_historical_sites(e.world, [{
        "tick": e.world.tick, "type": "war", "city_id": city.id,
        "title": f"Siege of {city.name}", "detail": "A real battle.",
    }])
    world_mod.tick(e.world)
    city = e.world.cities[city.id]
    assert city.trauma > 0
    assert 0 <= city.heritage <= 1


def test_knowledge_diffuses_through_real_city_contact(fresh_engine):
    w = fresh_engine.world
    coords = [(y, x) for y in range(5, w.height - 5)
              for x in range(5, w.width - 5)
              if w.land_mask[y, x]]
    a_pos = coords[0]
    b_pos = min(coords[1:], key=lambda p: abs(p[0] - a_pos[0]) + abs(p[1] - a_pos[1]))
    a = civ_mod.Civilization(id=w.new_civ_id(), name="A", origin_species_id=1,
                             founded_tick=w.tick)
    b = civ_mod.Civilization(id=w.new_civ_id(), name="B", origin_species_id=1,
                             founded_tick=w.tick)
    w.civilizations[a.id] = a
    w.civilizations[b.id] = b
    city_a = city_mod.found_city(w, a, a_pos[0], a_pos[1], 2500, name="A City")
    city_b = city_mod.found_city(w, b, b_pos[0], b_pos[1], 2500, name="B City")
    city_a.wealth = city_b.wealth = 60
    city_a.education = city_b.education = 0.8
    a.tech_domains = {"agriculture": 1.0, "metallurgy": 1.0, "navigation": 1.0,
                      "governance": 1.0, "medicine": 1.0, "warcraft": 1.0}
    b.tech_domains = {"agriculture": 0.0, "metallurgy": 0.0, "navigation": 0.0,
                      "governance": 0.0, "medicine": 0.0, "warcraft": 0.0}
    before = b.tech_domains["agriculture"]
    civ_mod._diffuse_technology(w, [a, b])
    assert b.tech_domains["agriculture"] > before


def test_historical_sites_feed_city_memory_pressure(fresh_engine):
    e = fresh_engine
    for _ in range(900):
        world_mod.tick(e.world)
    city = max((c for c in e.world.cities.values() if c.alive),
               key=lambda c: c.population)
    city.heritage = 0.0
    city.trauma = 0.0
    world_mod.remember_historical_sites(e.world, [
        {"tick": e.world.tick, "type": "settlement", "city_id": city.id,
         "title": f"Foundation of {city.name}", "detail": "A true founding memory."},
        {"tick": e.world.tick, "type": "collapse", "city_id": city.id,
         "title": f"Ruin near {city.name}", "detail": "A true collapse memory."},
    ])
    world_mod.tick(e.world)
    assert city.heritage > 0
    assert city.trauma > 0
