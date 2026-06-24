"""Factions — how micro incentives become macro politics.

Individuals found and join organizations that match their ideology and grievances: a
wealthy merchant raises a guild or trade league; the aggrieved and radical form a
revolutionary movement; the devout gather into a religious order; the martial into a
military order. Factions recruit, accumulate influence in their seat city, and act on
it — a league enriches its city, an order fortifies it, and a revolutionary movement
that gains enough influence in an unhappy city overthrows its rulers and founds a NEW
civilization. Empires here are not scripted; some are born from a single furious
generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..sim.civilization import Civilization, _CIV_NAMES
from ..agents.traits import ACTIONS

KINDS = ["guild", "merchant_league", "religious_order", "military_order",
         "secret_society", "revolutionary", "political_party"]

_NAME = {
    "guild": ["Guild of {c}", "{c} Artisans"],
    "merchant_league": ["{c} Trade League", "League of {c}"],
    "religious_order": ["Order of {c}", "Brethren of {c}"],
    "military_order": ["Sword-Order of {c}", "{c} Host"],
    "secret_society": ["The Veiled Hand of {c}", "Hidden {c}"],
    "revolutionary": ["The {c} Uprising", "Free {c} Movement", "Sons of {c}"],
    "political_party": ["{c} Assembly Party", "Reformers of {c}"],
}
_GOAL = {
    "guild": "monopolize a craft", "merchant_league": "control trade",
    "religious_order": "spread the faith", "military_order": "wage holy war",
    "secret_society": "rule from the shadows", "revolutionary": "overthrow the rulers",
    "political_party": "win control of the city",
}


@dataclass
class Faction:
    id: int
    name: str
    kind: str
    goal: str
    founder_id: int
    founder_name: str
    seat_city: int | None
    seat_city_name: str
    civ_id: int
    founded_tick: int
    member_ids: list[int] = field(default_factory=list)
    influence: float = 0.05            # 0..1 sway over the seat city
    religion_id: int | None = None
    history: list[str] = field(default_factory=list)
    alive: bool = True

    def member_count(self, world, base_pop=0) -> int:
        return len(self.member_ids) + base_pop


def step(society, world, population) -> list[dict]:
    out: list[dict] = []
    out += _maybe_found(society, world, population)
    _recruit(society, world, population)
    out += _exert_influence(society, world, population)
    return out


def _maybe_found(society, world, population) -> list[dict]:
    if not world.rng.chance("fac_found", 0.09):
        return []
    cand, kind = _find_founder(world, population)
    if cand is None:
        return []
    rng = world.rng.stream("faction")
    city = world.cities.get(cand.home_city)
    base = city.name if city else "the free folk"
    name = _pick(rng, _NAME[kind]).format(c=base)
    fid = society.nid()
    fac = Faction(id=fid, name=name, kind=kind, goal=_GOAL[kind],
                  founder_id=cand.id, founder_name=cand.name,
                  seat_city=cand.home_city, seat_city_name=base,
                  civ_id=cand.civ_id, founded_tick=world.tick,
                  member_ids=[cand.id], religion_id=cand.religion_id)
    fac.history.append(f"Founded by {cand.name} in {base}.")
    society.factions[fid] = fac
    cand.faction_ids.append(fid)
    cand.remember(f"I founded {name} to {fac.goal}.", "achievement", world.tick, 0.6)
    cand.milestones.append(f"Founded {name}.")
    return [{"tick": world.tick, "type": "faction_founded", "faction_id": fid,
             "civ_id": cand.civ_id, "title": f"{cand.name} founded {name}",
             "detail": f"In {base}, {cand.name} founded {name} to {fac.goal}.",
             "major": kind in ("revolutionary", "religious_order")}]


def _kind_scores(p):
    idg = p.ideology
    return {
        "merchant_league": 0.6 * idg.get("mercantilism", 0) + 0.4 * min(1, p.wealth / 20) + 0.3 * p.status,
        "revolutionary":   0.7 * p.grievance + 0.5 * idg.get("radicalism", 0),
        "religious_order": 0.7 * idg.get("piety", 0) + (0.4 if p.religion_id else 0),
        "military_order":  0.6 * idg.get("militarism", 0) + 0.3 * p.status,
        "guild":           0.4 * idg.get("mercantilism", 0) + 0.4 * p.skills.get("crafting", 0),
    }


def _find_founder(world, population):
    """Find the best would-be founder *for each kind*, then let conditions choose
    which faction is actually born — so the aggrieved raise revolts, the rich raise
    leagues, the devout raise orders. The kind emerges from who is ready to lead."""
    best_by_kind: dict[str, tuple] = {}     # kind -> (person, score)
    for p in population.people.values():
        if not p.alive or p.age < 20 or len(p.faction_ids) >= 2:
            continue
        for k, s in _kind_scores(p).items():
            if s > best_by_kind.get(k, (None, 0.95))[1]:
                best_by_kind[k] = (p, s)
    if not best_by_kind:                    # promote a city to find someone
        live = [c for c in world.cities.values() if c.alive]
        if live:
            rng = world.rng.stream("faction")
            population.focus(world, live[int(rng.integers(0, len(live)))].id)
        return None, None
    # choose the kind probabilistically, weighted by its champion's readiness
    rng = world.rng.stream("faction")
    kinds = list(best_by_kind)
    weights = [best_by_kind[k][1] ** 3 for k in kinds]
    total = sum(weights)
    r = rng.random() * total
    acc = 0.0
    for k, w in zip(kinds, weights):
        acc += w
        if r <= acc:
            return best_by_kind[k][0], k
    return best_by_kind[kinds[-1]][0], kinds[-1]


def _recruit(society, world, population) -> None:
    for fac in society.factions.values():
        if not fac.alive or fac.seat_city not in population.focus_cities:
            continue
        for p in population.residents(fac.seat_city):
            if fac.id in p.faction_ids or len(p.faction_ids) >= 2:
                continue
            city = world.cities.get(fac.seat_city)
            learned = _learned_tendency(world, p, _action_for_faction(fac), city)
            if (_appeals(p, fac) * learned > 0.55
                    and world.rng.stream("faction").random() < 0.3):
                p.faction_ids.append(fac.id)
                fac.member_ids.append(p.id)


def _appeals(p, fac) -> float:
    idg = p.ideology
    if fac.kind == "revolutionary":
        return 0.6 * p.grievance + 0.4 * idg.get("radicalism", 0)
    if fac.kind in ("merchant_league", "guild"):
        return 0.7 * idg.get("mercantilism", 0)
    if fac.kind == "religious_order":
        return 0.5 * idg.get("piety", 0) + (0.4 if p.religion_id == fac.religion_id else 0)
    if fac.kind == "military_order":
        return idg.get("militarism", 0)
    return 0.4 * idg.get("radicalism", 0)


def _action_for_faction(fac) -> str:
    if fac.kind in ("merchant_league", "guild", "political_party"):
        return "socialize"
    if fac.kind == "religious_order":
        return "worship"
    if fac.kind in ("military_order", "revolutionary"):
        return "feud"
    return "study"


def _learned_tendency(world, person, action: str, city) -> float:
    brain = getattr(world, "species_brain", None)
    if brain is None or action not in ACTIONS:
        return 1.0
    try:
        bias = brain.action_bias(person, city, world)
        return max(0.45, min(1.8, float(bias[ACTIONS.index(action)])))
    except Exception:  # noqa: BLE001
        return 1.0


def _exert_influence(society, world, population) -> list[dict]:
    out: list[dict] = []
    for fac in list(society.factions.values()):
        if not fac.alive:
            continue
        city = world.cities.get(fac.seat_city)
        if not city or not city.alive:
            fac.alive = False
            continue
        # influence grows from members and from the city's mood matching the cause
        member_pull = min(0.4, 0.02 * len(fac.member_ids))
        mood = city.unrest if fac.kind == "revolutionary" else min(1, city.wealth / 30)
        fac.influence = min(1.0, 0.97 * fac.influence + 0.05 * (member_pull + 0.3 * mood))

        out += _act(society, world, fac, city)
    return out


def _act(society, world, fac, city) -> list[dict]:
    if fac.kind in ("merchant_league", "guild") and fac.influence > 0.4:
        city.wealth += 0.3 * fac.influence
        city.culture += 0.05 * fac.influence
        civ = world.civilizations.get(fac.civ_id)
        if civ:
            world.params.adjust("tech_progress", 0.2)   # leagues fund progress
    elif fac.kind == "military_order" and fac.influence > 0.4:
        city.infrastructure = min(10.0, city.infrastructure + 0.02 * fac.influence)
    elif fac.kind == "revolutionary" and fac.influence > 0.6 and city.unrest > 0.5:
        return _revolution(society, world, fac, city)
    return []


def _revolution(society, world, fac, city) -> list[dict]:
    """The seat city overthrows its rulers and secedes as a new civilization."""
    old = world.civilizations.get(city.civ_id)
    if old and city.id in old.city_ids:
        old.city_ids.remove(city.id)
    cid = world.new_civ_id()
    name = f"Free State of {city.name}"
    civ = Civilization(id=cid, name=name,
                       origin_species_id=(old.origin_species_id if old else 0),
                       founded_tick=world.tick,
                       parent_civ_id=(old.id if old else None))
    # a revolutionary state inherits its parent's people and character, then drifts
    # markedly more radical — its origin is rebellion.
    if old is not None:
        from ..sim.civilization import apply_archetype, _shift_color
        apply_archetype(civ, {
            "people": old.people, "color": _shift_color(old.color),
            "ideology": "Revolutionary Republic", "stance": "expansionist",
            "traits": (old.cultural_traits[:1] + ["egalitarian", "defiant"]),
            "desires": ["freedom", "justice", "renewal"],
            "economic": old.economic_bias, "military": min(1.0, old.military_bias + 0.1),
            "religious": max(0.0, old.religious_bias - 0.2), "exploration": old.exploration_bias,
            "axes": {**old.ideology_axes,
                     "radicalism": min(1.0, old.ideology_axes.get("radicalism", 0.4) + 0.35)},
        })
    civ.capital_city_id = city.id
    civ.city_ids.append(city.id)
    civ.history.append(f"Born of the {fac.name}'s revolution in {city.name}.")
    world.civilizations[cid] = civ
    city.civ_id = cid
    city.unrest = 0.2                       # catharsis
    fac.influence = 0.3
    fac.history.append(f"Overthrew the old order in {city.name} (tick {world.tick}).")
    world.add_marker("revolution", city.pos[0], city.pos[1], ttl=140, label=city.name)
    return [{"tick": world.tick, "type": "revolution", "civ_id": cid,
             "faction_id": fac.id, "city_id": city.id,
             "title": f"Revolution in {city.name}",
             "detail": f"The {fac.name} overthrew {old.name if old else 'the rulers'} "
                       f"and proclaimed the {name}.", "major": True}]


def _pick(rng, seq):
    return seq[int(rng.integers(0, len(seq)))]
