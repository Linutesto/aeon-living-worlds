"""Tests for the discovery system (Phase 12) and city chronicle (Phase 11)."""

from __future__ import annotations

import json

from aeon.server.encoding import to_jsonable


def test_discoveries_are_grounded_and_focusable(grown_engine):
    d = grown_engine.discoveries()
    recs = d["discoveries"]
    assert recs, "a grown world should yield records"
    # every record must point at a real, focusable subject (truth, not fiction)
    kinds = {"city", "person", "religion", "faction", "civ"}
    for r in recs:
        assert r["focus"]["kind"] in kinds
        assert "title" in r and "subject" in r
    # records must be JSON-safe (no numpy leaks)
    json.dumps(to_jsonable(d), allow_nan=False)


def test_oldest_citizen_is_actually_oldest(grown_engine):
    e = grown_engine
    recs = {r["key"]: r for r in e.discoveries()["discoveries"]}
    if "oldest_citizen" not in recs:
        return
    pid = recs["oldest_citizen"]["focus"]["id"]
    p = e.population.get(pid)
    assert p is not None and p.alive
    oldest = max((q for q in e.population.people.values() if q.alive),
                 key=lambda q: q.age)
    assert p.age == oldest.age


def test_largest_city_matches_population(grown_engine):
    e = grown_engine
    recs = {r["key"]: r for r in e.discoveries()["discoveries"]}
    cid = recs["largest_city"]["focus"]["id"]
    biggest = max((c for c in e.world.cities.values() if c.alive),
                  key=lambda c: c.population)
    assert cid == biggest.id


def test_city_chronicle_is_truthful(grown_engine):
    e = grown_engine
    c = max((c for c in e.world.cities.values() if c.alive),
            key=lambda c: c.population)
    chron = e.city_chronicle(c)
    assert chron and chron[0].startswith("Founded")
    # the closing line reports the real population
    assert str(int(c.population)) in chron[-1]
    json.dumps(to_jsonable(chron), allow_nan=False)


def test_city_inspector_includes_chronicle(grown_engine):
    e = grown_engine
    c = next(c for c in e.world.cities.values() if c.alive)
    dossier = e.inspect_city(c.id)
    assert "chronicle" in dossier and isinstance(dossier["chronicle"], list)
    json.dumps(to_jsonable(dossier), allow_nan=False)
