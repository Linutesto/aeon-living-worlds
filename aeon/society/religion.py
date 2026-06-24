"""Religions — faiths that emerge from people and reshape the world.

A religion is *founded* by a real, charismatic, devout individual; it spreads city to
city through proximity and the conversion of materialized residents; it schisms when
its faithful diverge or its founder dies; and where it comes to dominate a
civilization it can ignite holy war against neighbours of another faith. Nothing here
is scripted — which faiths exist, and where, is a product of who believed what.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import beliefs as _b
from ..agents.traits import ACTIONS

_PREFIX = ["Church of", "Order of", "Cult of", "Way of", "Covenant of", "Faith of"]
_DEITY = ["the Dawn", "the Deep", "the Ember", "the Harvest", "the Thousand Eyes",
          "the Sleeping God", "the Tide", "the Iron Sky", "the Ancestors",
          "the Green Mother", "the Pale Star", "the Final Silence"]
_TENETS = [
    "The harvest is sacred; waste is sin.", "Strength is the gods' favour.",
    "All souls are equal before death.", "Wealth is a trust, not a right.",
    "The old ways must never be forgotten.", "Suffering purifies the spirit.",
    "Knowledge is the highest prayer.", "Blood debts must be repaid.",
    "The sea remembers every drowned name.", "Kings are chosen, not born.",
]


@dataclass
class Religion:
    id: int
    name: str
    founder_id: int
    founder_name: str
    tenets: list[str]
    holy_city: int | None
    holy_city_name: str
    civ_origin: int
    founded_tick: int
    cities: dict[int, float] = field(default_factory=dict)   # city_id -> share 0..1
    schism_parent: int | None = None
    last_schism: int = -9999
    history: list[str] = field(default_factory=list)
    alive: bool = True

    def follower_estimate(self, world) -> int:
        tot = 0.0
        for cid, share in self.cities.items():
            c = world.cities.get(cid)
            if c and c.alive:
                tot += share * c.population
        return int(tot)

    def dominant_cities(self, society, world) -> list[int]:
        out = []
        for cid, share in self.cities.items():
            dom, s = society.religion_of_city(cid)
            if dom and dom.id == self.id and share > 0.4:
                out.append(cid)
        return out


def step(society, world, population) -> list[dict]:
    out: list[dict] = []
    out += _maybe_found(society, world, population)
    _spread(society, world, population)
    _adopt(society, world, population)
    out += _maybe_schism(society, world, population)
    out += _maybe_holy_war(society, world)
    return out


def _maybe_found(society, world, population) -> list[dict]:
    if not world.rng.chance("relig_found", 0.06):
        return []
    live_cities = [c for c in world.cities.values() if c.alive]
    if not live_cities:
        return []
    # find a devout, charismatic founder — promote a city's residents if needed
    cand = _best_prophet(population)
    if cand is None:
        rng = world.rng.stream("religion")
        city = live_cities[int(rng.integers(0, len(live_cities)))]
        population.focus(world, city.id)
        cand = _best_prophet(population, city.id)
    if cand is None:
        return []
    rng = world.rng.stream("religion")
    name = f"{_pick(rng, _PREFIX)} {_pick(rng, _DEITY)}"
    tenets = list({_pick(rng, _TENETS) for _ in range(3)})
    if cand.beliefs:
        tenets.append(cand.beliefs[0])
    rid = society.nid()
    city = world.cities.get(cand.home_city)
    rel = Religion(id=rid, name=name, founder_id=cand.id, founder_name=cand.name,
                   tenets=tenets, holy_city=cand.home_city,
                   holy_city_name=city.name if city else "the wilds",
                   civ_origin=cand.civ_id, founded_tick=world.tick)
    rel.cities[cand.home_city] = 0.5
    rel.history.append(f"Founded by {cand.name} at {rel.holy_city_name}.")
    society.religions[rid] = rel
    cand.religion_id = rid
    cand.ideology["piety"] = 1.0
    cand.remember(f"I founded {name}.", "faith", world.tick, 0.9)
    cand.milestones.append(f"Founded {name}.")
    if city:
        world.add_marker("religion", city.pos[0], city.pos[1], ttl=120, label=name)
    return [{"tick": world.tick, "type": "religion_founded", "religion_id": rid,
             "civ_id": cand.civ_id, "title": f"{cand.name} founded {name}",
             "detail": f"At {rel.holy_city_name}, {cand.name} proclaimed {name}: "
                       f"\"{tenets[0]}\"", "major": True}]


def _best_prophet(population, city_id=None):
    best, score = None, 0.95         # require genuine charisma+piety
    for p in population.people.values():
        if not p.alive or p.religion_id is not None or p.age < 18:
            continue
        if city_id is not None and p.home_city != city_id:
            continue
        s = (p.ideology.get("piety", 0) + p.status
             + p.personality.get("extraversion", .5))
        if s > score:
            best, score = p, s
    return best


def _spread(society, world, population) -> None:
    rng = world.rng.stream("religion")
    cells = world.cities
    for rel in society.religions.values():
        if not rel.alive:
            continue
        # deepen where present
        for cid in list(rel.cities):
            rel.cities[cid] = min(1.0, rel.cities[cid] + 0.01)
        # diffuse to a nearby city (trade/proximity carries faith)
        if rng.random() < 0.5:
            seed = rng.choice(list(rel.cities)) if rel.cities else None
            src = cells.get(int(seed)) if seed is not None else None
            if src:
                near = _nearest_city(world, src, exclude=set(rel.cities))
                if near and (abs(near.pos[0]-src.pos[0]) + abs(near.pos[1]-src.pos[1])) < 40:
                    rel.cities[near.id] = max(rel.cities.get(near.id, 0), 0.15)
        # drop dead cities
        rel.cities = {c: s for c, s in rel.cities.items()
                      if c in cells and cells[c].alive}
        if not rel.cities:
            rel.alive = False


def _adopt(society, world, population) -> None:
    """Materialized residents of focused cities adopt their city's dominant faith."""
    for cid in population.focus_cities:
        city = world.cities.get(cid)
        dom, share = society.religion_of_city(cid)
        if not dom or share < 0.3:
            continue
        for p in population.residents(cid):
            if p.religion_id == dom.id:
                continue
            learned = _learned_tendency(world, p, "worship", city)
            chance = _b.conversion_susceptibility(p, dom) * share * learned
            if world.rng.stream("religion").random() < min(0.95, chance):
                p.religion_id = dom.id
                p.remember(f"I came to the {dom.name}.", "faith", world.tick, 0.4)


def _learned_tendency(world, person, action: str, city) -> float:
    brain = getattr(world, "species_brain", None)
    if brain is None or action not in ACTIONS:
        return 1.0
    try:
        bias = brain.action_bias(person, city, world)
        return max(0.45, min(1.8, float(bias[ACTIONS.index(action)])))
    except Exception:  # noqa: BLE001
        return 1.0


def _maybe_schism(society, world, population) -> list[dict]:
    out: list[dict] = []
    if sum(1 for r in society.religions.values() if r.alive) > 40:
        return out                       # the world is sectarian enough
    for rel in list(society.religions.values()):
        if not rel.alive:
            continue
        if world.tick - rel.last_schism < 200 or world.tick - rel.founded_tick < 120:
            continue                     # cooldown: faiths don't fracture weekly
        founder = population.get(rel.founder_id)
        founder_dead = founder is not None and not founder.alive
        big = len(rel.cities) >= 5
        if not (big and (founder_dead or world.rng.chance("schism", 0.01))):
            continue
        rel.last_schism = world.tick
        # a reformer breaks away with the more radical congregations
        rng = world.rng.stream("religion")
        cids = sorted(rel.cities, key=lambda c: rel.cities[c])
        take = cids[: max(1, len(cids) // 3)]
        reformer = _best_prophet(population) or founder
        rid = society.nid()
        name = f"Reformed {rel.name}" if rng.random() < 0.5 else \
               f"{_pick(rng, _PREFIX)} {_pick(rng, _DEITY)}"
        new = Religion(id=rid, name=name, founder_id=reformer.id if reformer else 0,
                       founder_name=reformer.name if reformer else "a heretic",
                       tenets=rel.tenets[:2] + [_pick(rng, _TENETS)],
                       holy_city=take[0], holy_city_name=world.cities[take[0]].name
                       if take[0] in world.cities else "?",
                       civ_origin=rel.civ_origin, founded_tick=world.tick,
                       schism_parent=rel.id)
        for c in take:
            new.cities[c] = rel.cities.pop(c)
        new.history.append(f"Broke from {rel.name} in a great schism.")
        society.religions[rid] = new
        out.append({"tick": world.tick, "type": "schism", "religion_id": rid,
                    "title": f"The {name} broke from {rel.name}",
                    "detail": f"A schism split the faithful of {rel.name}; "
                              f"the {name} now keeps its own holy city at "
                              f"{new.holy_city_name}.", "major": True})
    return out


def _maybe_holy_war(society, world) -> list[dict]:
    out: list[dict] = []
    for civ in world.civilizations.values():
        if not civ.alive:
            continue
        faith = _civ_faith(society, world, civ)
        if faith is None:
            continue
        for other in world.civilizations.values():
            if other.id <= civ.id or not other.alive:
                continue
            ofaith = _civ_faith(society, world, other)
            if ofaith is None or ofaith.id == faith.id:
                continue
            if world.rng.chance("holy_war", 0.01 * world.params.war_propensity):
                fc = civ.cities(world); tc = other.cities(world)
                if not fc or not tc:
                    continue
                pair = min(((a, b) for a in fc for b in tc),
                           key=lambda t: abs(t[0].pos[0]-t[1].pos[0]) + abs(t[0].pos[1]-t[1].pos[1]))
                civ.war_intents.append({"from_city": pair[0].id, "to_city": pair[1].id})
                out.append({"tick": world.tick, "type": "holy_war", "civ_id": civ.id,
                            "title": f"Holy war: {civ.name} against {other.name}",
                            "detail": f"The {faith.name} of {civ.name} marched against "
                                      f"the {ofaith.name} of {other.name}.", "major": True})
    return out


def _civ_faith(society, world, civ):
    """The dominant religion across a civ's cities, if any commands a majority."""
    tally: dict[int, float] = {}
    cs = civ.cities(world)
    for c in cs:
        dom, share = society.religion_of_city(c.id)
        if dom and share > 0.5:
            tally[dom.id] = tally.get(dom.id, 0) + 1
    if not tally or not cs:
        return None
    rid = max(tally, key=tally.get)
    if tally[rid] / len(cs) < 0.5:
        return None
    return society.religions.get(rid)


def _nearest_city(world, src, exclude):
    best, bd = None, 1e9
    for c in world.cities.values():
        if not c.alive or c.id == src.id or c.id in exclude:
            continue
        d = abs(c.pos[0]-src.pos[0]) + abs(c.pos[1]-src.pos[1])
        if d < bd:
            best, bd = c, d
    return best


def _pick(rng, seq):
    return seq[int(rng.integers(0, len(seq)))]
