"""Tests for the civilization overhaul: a plural world of distinct nations.

The world must START as five rival civilizations with real, different characters,
those nations must be inherited by their cities and citizens, the political lifecycle
(collapse / merge / split / golden age) must be reachable and produce real history
events, and all of it must serialize for the renderer/timeline.
"""

from __future__ import annotations

import json

from aeon.config import load_config
from aeon.sim import world as world_mod
from aeon.sim import civilization as civ_mod
from aeon.server.encoding import to_jsonable


def _fresh_world(seed: int = 7):
    cfg = load_config()
    cfg.world.seed = seed
    cfg.persistence.enabled = False
    return world_mod.create_world(cfg)


# ---------------- genesis: five distinct nations ----------------

def test_world_starts_with_five_distinct_civilizations():
    w = _fresh_world()
    civs = [c for c in w.civilizations.values() if c.alive]
    assert len(civs) == 5, "the world should open as five rival nations"
    # each has a capital that really exists
    for c in civs:
        assert c.capital_city_id in w.cities
        assert w.cities[c.capital_city_id].civ_id == c.id
    # the nations are genuinely distinct, not five copies
    assert len({c.name for c in civs}) == 5
    assert len({c.ideology for c in civs}) == 5
    assert len({c.color for c in civs}) == 5
    assert len({c.diplomatic_stance for c in civs}) >= 3


def test_each_civ_has_full_identity():
    w = _fresh_world()
    for c in w.civilizations.values():
        assert c.people and isinstance(c.people, str)
        assert c.ideology_axes and set(c.ideology_axes) == set(civ_mod.IDEOLOGY_AXES)
        assert c.cultural_traits, "a nation needs cultural traits"
        assert c.preferred_desires
        for bias in (c.economic_bias, c.military_bias, c.religious_bias,
                     c.exploration_bias):
            assert 0.0 <= bias <= 1.0


def test_genesis_emits_founding_events():
    w = _fresh_world()
    founds = [e for e in w.genesis_events if e["type"] == "civilization"]
    assert len(founds) == 5
    for e in founds:
        assert e.get("major") is True
        assert e["civ_id"] in w.civilizations
        assert "ideology" in e.get("why", {})


def test_capitals_are_spaced_apart():
    w = _fresh_world()
    caps = [w.cities[c.capital_city_id].pos for c in w.civilizations.values()
            if c.capital_city_id in w.cities]
    for i, a in enumerate(caps):
        for b in caps[i + 1:]:
            assert abs(a[0] - b[0]) + abs(a[1] - b[1]) >= civ_mod._cities.MIN_CITY_SPACING


# ---------------- citizens inherit civ identity ----------------

def test_citizens_inherit_their_nations_character():
    from aeon.agents.population import PopulationManager
    cfg = load_config()
    cfg.world.seed = 7
    cfg.persistence.enabled = False
    w = world_mod.create_world(cfg)
    for _ in range(500):
        world_mod.tick(w)
    pm = PopulationManager(cfg)
    # find a strongly traditionalist civ and a strongly radical-leaning one
    civs = {c.id: c for c in w.civilizations.values() if c.alive and c.cities(w)}
    trad = max(civs.values(), key=lambda c: c.ideology_axes.get("traditionalism", 0))
    cap = w.cities.get(trad.capital_city_id) or trad.cities(w)[0]
    pm.focus(w, cap.id)
    res = pm.residents(cap.id)
    assert res, "a focused capital should have residents"
    # citizens carry the nation's people-name and a coherent slice of its ideology
    assert all(p.species == trad.people for p in res)
    assert all(p.civ_id == trad.id for p in res)
    avg_trad = sum(p.ideology.get("traditionalism", 0) for p in res) / len(res)
    assert avg_trad > 0.4, "a traditionalist nation should breed traditionalist citizens"


def test_citizens_are_not_all_identical():
    from aeon.agents.population import PopulationManager
    cfg = load_config()
    cfg.world.seed = 7
    cfg.persistence.enabled = False
    w = world_mod.create_world(cfg)
    for _ in range(600):
        world_mod.tick(w)
    pm = PopulationManager(cfg)
    live = sorted((c for c in w.cities.values() if c.alive),
                  key=lambda c: -c.population)[:6]
    for c in live:
        pm.focus(w, c.id)
    people = list(pm.people.values())
    assert len(people) > 30
    # a believable spread of classes — not a city of nobles
    classes = {p.social_class for p in people}
    assert len(classes) >= 4
    noble_share = sum(1 for p in people if p.social_class == "noble") / len(people)
    assert noble_share < 0.4, "the population should not be overwhelmingly noble"
    # individuating colour really varies
    assert len({p.quirk for p in people}) >= 5
    assert len({p.life_goal for p in people}) >= 5
    # health and grievance are no longer uniform
    assert len({round(p.health, 1) for p in people}) >= 3
    assert max(p.grievance for p in people) > min(p.grievance for p in people)


# ---------------- lifecycle ----------------

def test_collapse_when_a_civ_loses_its_last_city():
    w = _fresh_world()
    civ = next(c for c in w.civilizations.values() if c.alive)
    for cid in list(civ.city_ids):
        city = w.cities.get(cid)
        if city:
            city.abandoned_tick = w.tick
    events = civ_mod.step(w)
    assert civ.collapsed_tick is not None
    assert civ.status == "collapsed"
    assert any(e["type"] == "collapse" and e["civ_id"] == civ.id for e in events)


def test_merge_unites_two_friendly_neighbours():
    w = _fresh_world()
    civs = [c for c in w.civilizations.values() if c.alive]
    a, b = civs[0], civs[1]
    # force the merge preconditions: small, friendly, adjacent
    a.relations[b.id] = b.relations[a.id] = 0.9
    bc = w.cities[b.capital_city_id]
    ac = w.cities[a.capital_city_id]
    bc.pos = (ac.pos[0] + 4, ac.pos[1] + 4)
    events = civ_mod._maybe_merge(w, [a, b])
    assert events, "a friendly adjacent pair should be able to merge"
    merged = a if a.merged_into is not None else b
    survivor = b if merged is a else a
    assert merged.merged_into == survivor.id
    assert not merged.alive
    assert merged.city_ids == []
    # the survivor absorbed the merged civ's cities
    assert all(w.cities[cid].civ_id == survivor.id for cid in survivor.city_ids)


def test_split_spawns_a_successor_state():
    w = _fresh_world()
    civ = next(c for c in w.civilizations.values() if c.alive)
    cap = w.cities[civ.capital_city_id]
    # give the civ several disaffected, distant cities
    for i in range(3):
        far = civ_mod._cities.found_city(
            w, civ, min(w.height - 2, cap.pos[0] + 35 + i),
            min(w.width - 2, cap.pos[1] + 35), population=2000.0)
        far.unrest = 0.7
    civ.status = "declining"
    before = len(w.civilizations)
    events = civ_mod._maybe_split(w, [civ])
    assert events, "a large unstable civ should fracture"
    assert len(w.civilizations) == before + 1
    succ = w.civilizations[max(w.civilizations)]
    assert succ.parent_civ_id == civ.id
    assert succ.city_ids and succ.capital_city_id in w.cities
    assert events[0]["type"] == "schism"


def test_golden_age_fires_for_a_flourishing_civ():
    w = _fresh_world()
    civ = next(c for c in w.civilizations.values() if c.alive)
    cap = w.cities[civ.capital_city_id]
    second = civ_mod._cities.found_city(w, civ, cap.pos[0] + 16, cap.pos[1], population=5000.0)
    for c in (cap, second):
        c.culture = 200
        c.wealth = 80
        c.civic_stability = 0.9
    # _golden_ages is gated by an rng chance; try enough times to make it reachable
    fired = []
    for _ in range(200):
        fired += civ_mod._golden_ages(w, [civ])
        if fired:
            break
        civ.golden_age_tick = None
    assert fired, "a rich, cultured, stable civ should eventually enjoy a golden age"
    assert fired[0]["type"] == "golden_age"


# ---------------- serialization ----------------

def test_civ_identity_serializes_for_the_renderer():
    cfg = load_config()
    cfg.governor.enabled = False
    cfg.persistence.enabled = False
    cfg.mind.enabled = False
    from aeon.engine import Engine
    engine = Engine(cfg)
    payload = engine.serialize_cities()
    civs = payload["civs"]
    assert len(civs) >= 5
    sample = civs[0]
    for key in ("people", "color", "ideology", "stance", "traits", "desires",
                "biases", "ideology_axes", "status", "capital_id"):
        assert key in sample, f"civ payload missing {key}"
    json.dumps(to_jsonable(payload), allow_nan=False)


def test_genesis_founding_events_reach_the_timeline():
    cfg = load_config()
    cfg.governor.enabled = False
    cfg.persistence.enabled = False
    cfg.mind.enabled = False
    from aeon.engine import Engine
    engine = Engine(cfg)
    civ_events = engine.history.filter(type="civilization", limit=50)
    assert len(civ_events) >= 5, "the founding of the nations should be in the timeline"
