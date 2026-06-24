"""Tests for the LLM interpretation layer (biographies + newspaper).

These test the *grounding* and *caching* — the parts that must be correct regardless
of whether an LLM is reachable. The actual prose generation is exercised separately
in live verification (it needs Ollama).
"""

from __future__ import annotations

import asyncio
import json

from aeon.society import interpret as I
from aeon.server.encoding import to_jsonable


def _resident(engine):
    c = max((c for c in engine.world.cities.values() if c.alive),
            key=lambda c: c.population)
    engine.population.focus(engine.world, c.id)
    return engine.population.residents(c.id)[0]


def test_biography_facts_are_grounded(grown_engine):
    e = grown_engine
    p = _resident(e)
    facts = I.build_biography_facts(p, e.world, e.society, e.life_chronicle(p),
                                    e.family_tree(p.id))
    # the fact-sheet must contain the person's real, verifiable details
    assert p.name in facts
    assert str(p.age) in facts
    assert p.profession in facts
    # it must not be empty boilerplate
    assert "LIFE EVENTS" in facts and "DEEDS" in facts


def test_cache_regenerates_only_on_signature_change():
    c = I.Cache()
    assert c.get("bio", 1, "sigA") is None
    c.put("bio", 1, "sigA", "A grounded life.")
    assert c.get("bio", 1, "sigA") == "A grounded life."   # cache hit
    assert c.get("bio", 1, "sigB") is None                 # life advanced → miss


def test_person_signature_changes_with_life(grown_engine):
    p = _resident(grown_engine)
    sig1 = I.person_signature(p)
    p.milestones.append("Did a great deed.")
    assert I.person_signature(p) != sig1                   # new milestone → regenerate


def test_biography_endpoint_is_json_safe_offline(grown_engine):
    # offline LLM → graceful result, still well-formed + JSON-safe
    grown_engine.governor.llm.online = False
    p = _resident(grown_engine)
    bio = asyncio.run(grown_engine.biography(p.id))
    assert bio["id"] == p.id and "biography" in bio
    json.dumps(to_jsonable(bio), allow_nan=False)


def test_city_facts_are_grounded(grown_engine):
    e = grown_engine
    c = max((c for c in e.world.cities.values() if c.alive), key=lambda c: c.population)
    civ = e.world.civilizations.get(c.civ_id)
    rel, share = e.society.religion_of_city(c.id)
    facts = I.build_city_facts(c, e.world, civ, rel, share, e.city_chronicle(c))
    assert c.name in facts and c.specialty in facts and "RECORDED HISTORY" in facts
    assert str(int(c.population)) in facts


def test_city_history_endpoint_offline(grown_engine):
    grown_engine.governor.llm.online = False
    c = max((c for c in grown_engine.world.cities.values() if c.alive),
            key=lambda c: c.population)
    out = asyncio.run(grown_engine.city_history(c.id))
    assert out["id"] == c.id and "history" in out
    json.dumps(to_jsonable(out), allow_nan=False)


def test_religion_history_grounded(grown_engine):
    e = grown_engine
    rels = [r for r in e.society.religions.values() if r.alive]
    if not rels:
        return
    r = rels[0]
    facts = I.build_religion_facts(r, e.world, e.population, r.follower_estimate(e.world))
    assert r.name in facts and r.holy_city_name in facts
    # must not invent doctrine — only the real tenets appear
    for tenet in r.tenets:
        assert tenet in facts
    e.governor.llm.online = False
    out = asyncio.run(e.religion_history(r.id))
    assert out["id"] == r.id and "history" in out


def test_culture_facts_are_grounded(grown_engine):
    e = grown_engine
    cultures = [c for c in e.society.cultures.values() if c.alive]
    if not cultures:
        return
    culture = cultures[0]
    city = e.world.cities.get(culture.origin_city)
    facts = I.build_culture_facts(culture, e.world, city, culture.history)
    assert culture.name in facts and culture.origin_city_name in facts
    for value in culture.values:
        assert value in facts


def test_background_narration_job_uses_event_ids(grown_engine):
    e = grown_engine
    city = max((c for c in e.world.cities.values() if c.alive), key=lambda c: c.population)
    job = e._narration_job_for_event({
        "id": 999, "tick": e.world.tick, "type": "settlement",
        "city_id": city.id, "title": f"{city.name} was founded", "detail": "",
    })
    assert job is not None
    assert job["kind"] == "city" and job["key"] == city.id
    assert city.name in job["facts"]


def test_newspaper_is_rate_limited(grown_engine):
    e = grown_engine
    e.governor.llm.online = False
    n1 = asyncio.run(e.newspaper())
    # immediate second call within the rate window returns the same (cached) tick
    e._newspaper = {"tick": e.world.tick, "items": "Old news."}
    n2 = asyncio.run(e.newspaper())
    assert n2["cached"] is True and n2["items"] == "Old news."
    json.dumps(to_jsonable(n1), allow_nan=False)
