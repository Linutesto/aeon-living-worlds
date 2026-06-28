"""Civilizations — peoples that own cities, expand territory, and make war.

A civilization is the political layer above cities. It emerges when a large, settled
people (a thriving herbivore species) concentrates on land that can support a town;
its first city is founded here and the rest of its growth happens in cities.py. This
module owns the things that span cities: technology, diplomacy/relations, and the
*decision* to go to war — which it expresses as a war intent that units.py turns into
an actual army marching across the map. It never scripts outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import world as _w
from . import species as _sp
from . import cities as _cities

_CIV_NAMES = ["Thalassa", "Veyran Dominion", "Ossuary League", "Kethari", "Molthar",
              "Aurun Concord", "Zhin Empire", "Brammish Folk", "Caelid", "Drosan Pact",
              "Sundered Houses", "Fyrelands", "Gormwell", "Halcyon States"]

# The ideology axes are the same ones society/beliefs.py derives for individuals, so a
# civ's national character and its citizens' inner lives speak the same language.
IDEOLOGY_AXES = ("piety", "radicalism", "militarism", "mercantilism", "traditionalism")


# Distinct national characters seeded at genesis. Each is a real bundle of identity that
# downstream systems read: the renderer colours territory; population.py seeds citizen
# ideology/beliefs/professions; diplomacy/war/merge/split use the biases; the governor
# prompt names the ideology. Nothing here scripts outcomes — it sets pressures.
CIV_ARCHETYPES: list[dict] = [
    {"name": "Solari Dominion", "people": "Solari", "ideology": "Theocracy",
     "color": "#e8b339", "stance": "zealous",
     "traits": ["devout", "ceremonial", "hierarchical"],
     "desires": ["worship", "order", "monuments"],
     "economic": 0.30, "military": 0.55, "religious": 0.95, "exploration": 0.30,
     "axes": {"piety": 0.90, "radicalism": 0.20, "militarism": 0.50,
              "mercantilism": 0.30, "traditionalism": 0.80}},
    {"name": "Varan Mercantile League", "people": "Varani", "ideology": "Mercantile Republic",
     "color": "#2bb6a8", "stance": "mercantile",
     "traits": ["pragmatic", "cosmopolitan", "acquisitive"],
     "desires": ["wealth", "trade", "novelty"],
     "economic": 0.95, "military": 0.30, "religious": 0.20, "exploration": 0.70,
     "axes": {"piety": 0.20, "radicalism": 0.30, "militarism": 0.30,
              "mercantilism": 0.90, "traditionalism": 0.30}},
    {"name": "Kragmar Warhost", "people": "Kragmar", "ideology": "Militarist Confederacy",
     "color": "#d44b3a", "stance": "belligerent",
     "traits": ["martial", "honour-bound", "fierce"],
     "desires": ["conquest", "glory", "strength"],
     "economic": 0.40, "military": 0.95, "religious": 0.40, "exploration": 0.50,
     "axes": {"piety": 0.40, "radicalism": 0.40, "militarism": 0.90,
              "mercantilism": 0.30, "traditionalism": 0.60}},
    {"name": "Sylvan Concord", "people": "Sylvani", "ideology": "Naturalist Commune",
     "color": "#4caf6a", "stance": "isolationist",
     "traits": ["harmonious", "insular", "patient"],
     "desires": ["harmony", "knowledge", "preservation"],
     "economic": 0.40, "military": 0.30, "religious": 0.50, "exploration": 0.30,
     "axes": {"piety": 0.50, "radicalism": 0.20, "militarism": 0.20,
              "mercantilism": 0.20, "traditionalism": 0.80}},
    {"name": "Aurelian Scholars", "people": "Aurelians", "ideology": "Technocracy",
     "color": "#5b8def", "stance": "diplomatic",
     "traits": ["inquisitive", "meritocratic", "rational"],
     "desires": ["knowledge", "progress", "discovery"],
     "economic": 0.60, "military": 0.30, "religious": 0.20, "exploration": 0.80,
     "axes": {"piety": 0.20, "radicalism": 0.40, "militarism": 0.30,
              "mercantilism": 0.50, "traditionalism": 0.20}},
    {"name": "Nomad Hordes of Esh", "people": "Eshkin", "ideology": "Nomad Clans",
     "color": "#e08a3c", "stance": "opportunist",
     "traits": ["restless", "free", "opportunist"],
     "desires": ["freedom", "plunder", "movement"],
     "economic": 0.50, "military": 0.70, "religious": 0.30, "exploration": 0.90,
     "axes": {"piety": 0.30, "radicalism": 0.60, "militarism": 0.60,
              "mercantilism": 0.50, "traditionalism": 0.30}},
    {"name": "Thornehold Monarchy", "people": "Thornefolk", "ideology": "Feudal Monarchy",
     "color": "#8a5bbf", "stance": "expansionist",
     "traits": ["noble", "rigid", "proud"],
     "desires": ["order", "legacy", "dominion"],
     "economic": 0.50, "military": 0.60, "religious": 0.50, "exploration": 0.40,
     "axes": {"piety": 0.60, "radicalism": 0.10, "militarism": 0.50,
              "mercantilism": 0.40, "traditionalism": 0.90}},
    {"name": "Tideborn Covenant", "people": "Tideborn", "ideology": "Seafaring Covenant",
     "color": "#2fb3d6", "stance": "mercantile",
     "traits": ["seafaring", "mystic", "adaptive"],
     "desires": ["exploration", "trade", "mystery"],
     "economic": 0.70, "military": 0.40, "religious": 0.60, "exploration": 0.90,
     "axes": {"piety": 0.60, "radicalism": 0.30, "militarism": 0.30,
              "mercantilism": 0.60, "traditionalism": 0.40}},
]

_STANCE_FALLBACK = "neutral"


@dataclass
class Civilization:
    id: int
    name: str
    origin_species_id: int
    founded_tick: int
    city_ids: list[int] = field(default_factory=list)
    tech: float = 0.0
    tech_domains: dict[str, float] = field(default_factory=dict)
    tech_milestones: dict[str, int] = field(default_factory=dict)
    relations: dict[int, float] = field(default_factory=dict)    # civ_id -> -1..1
    war_intents: list[dict] = field(default_factory=list)        # consumed by units.py
    history: list[str] = field(default_factory=list)
    collapsed_tick: int | None = None

    # --- identity (seeded from an archetype; successors inherit, drift over time) ---
    people: str = "Folk"                  # the lineage/culture name its citizens carry
    color: str = "#9b8cff"                # territory tint in the renderer
    ideology: str = "Tribal"             # named national character
    ideology_axes: dict[str, float] = field(default_factory=dict)  # piety, radicalism…
    cultural_traits: list[str] = field(default_factory=list)
    preferred_desires: list[str] = field(default_factory=list)
    economic_bias: float = 0.5
    military_bias: float = 0.5
    religious_bias: float = 0.5
    exploration_bias: float = 0.5
    diplomatic_stance: str = _STANCE_FALLBACK

    # --- lifecycle bookkeeping (collapse / merge / split / successor) ---
    capital_city_id: int | None = None
    parent_civ_id: int | None = None      # set for successor/splinter civs
    status: str = "rising"               # rising | stable | declining | collapsed | merged
    merged_into: int | None = None
    golden_age_tick: int | None = None

    def cities(self, world):
        return [world.cities[c] for c in self.city_ids if c in world.cities
                and world.cities[c].alive]

    def population_of(self, world) -> float:
        return sum(c.population for c in self.cities(world))

    @property
    def alive(self) -> bool:
        return self.collapsed_tick is None and self.merged_into is None


def apply_archetype(civ: Civilization, arch: dict) -> None:
    """Stamp an archetype's identity onto a civilization (used at genesis and when a
    successor inherits a parent's character before it drifts)."""
    civ.people = arch["people"]
    civ.color = arch["color"]
    civ.ideology = arch["ideology"]
    civ.ideology_axes = dict(arch["axes"])
    civ.cultural_traits = list(arch["traits"])
    civ.preferred_desires = list(arch["desires"])
    civ.economic_bias = float(arch["economic"])
    civ.military_bias = float(arch["military"])
    civ.religious_bias = float(arch["religious"])
    civ.exploration_bias = float(arch["exploration"])
    civ.diplomatic_stance = arch["stance"]


def _ensure_identity(civ: Civilization) -> None:
    """Repair civs loaded from old autosaves (pre-identity) or built by the social
    layer with only the core fields, so every civ has a coherent character."""
    if getattr(civ, "ideology_axes", None):
        return
    arch = CIV_ARCHETYPES[(civ.id - 1) % len(CIV_ARCHETYPES)]
    # keep any name the social layer chose (e.g. "Free State of …"); fill the rest.
    saved_name = civ.name
    apply_archetype(civ, arch)
    civ.name = saved_name
    if not civ.ideology_axes:
        civ.ideology_axes = {a: 0.5 for a in IDEOLOGY_AXES}


def step(world: "_w.WorldState") -> list[dict]:
    out: list[dict] = []
    for civ in world.civilizations.values():
        _ensure_identity(civ)
    out += _maybe_emerge(world)
    p = world.params
    civs = [c for c in world.civilizations.values() if c.alive]

    for civ in civs:
        cities = civ.cities(world)
        if not cities:
            civ.collapsed_tick = world.tick
            civ.status = "collapsed"
            out.append({"tick": world.tick, "type": "collapse", "civ_id": civ.id,
                        "title": f"The {civ.name} fell",
                        "detail": f"With no cities left, {civ.name} is no more.",
                        "major": True})
            continue
        # the capital is the most populous surviving city; track it for renderer/UI
        capital = max(cities, key=lambda c: c.population)
        civ.capital_city_id = capital.id
        _update_status(world, civ, cities)
        # tech advances with the most developed city and overall scale
        best_infra = max(c.infrastructure for c in cities)
        _ensure_tech(civ)
        knowledge = sum(getattr(c, "stocks", {}).get("knowledge", 0.0) for c in cities)
        archives = sum(getattr(c, "buildings", {}).get("archives", 0) for c in cities)
        markets = sum(getattr(c, "buildings", {}).get("market", 0) for c in cities)
        docks = sum(getattr(c, "buildings", {}).get("docks", 0) for c in cities)
        barracks = sum(getattr(c, "buildings", {}).get("barracks", 0) for c in cities)
        mines = sum(getattr(c, "buildings", {}).get("mines", 0) for c in cities)
        farms = sum(getattr(c, "buildings", {}).get("farms", 0) for c in cities)
        domain_gain = 0.0004 * p.tech_progress
        civ.tech_domains["agriculture"] += domain_gain * (farms + knowledge * 0.01)
        civ.tech_domains["metallurgy"] += domain_gain * (mines + knowledge * 0.008)
        civ.tech_domains["navigation"] += domain_gain * (docks + markets * 0.15)
        civ.tech_domains["governance"] += domain_gain * (best_infra + len(cities) * 0.2)
        civ.tech_domains["medicine"] += domain_gain * (knowledge * 0.006 + sum(1 for c in cities if c.plague > 0) * 2)
        civ.tech_domains["warcraft"] += domain_gain * (barracks + world.params.war_propensity)
        civ.tech = sum(civ.tech_domains.values()) / len(civ.tech_domains)

    out += _diffuse_technology(world, [c for c in civs if c.alive])
    out += _diplomacy_and_war(world, [c for c in civs if c.alive])
    out += lifecycle_step(world, [c for c in civs if c.alive])
    return out


def _maybe_emerge(world) -> list[dict]:
    out: list[dict] = []
    n_live = sum(1 for c in world.cities.values() if c.alive)
    cap = int(_cities.CITY_CAP * max(0.5, min(2.0, world.params.city_density)))
    if n_live >= cap:
        return out
    if not world.rng.chance("civ_emerge", 0.03 * world.params.civ_expansion_drive):
        return out
    # a people must exist: a large settled herbivore lineage
    candidates = [s for s in world.species.values()
                  if s.alive and s.diet == _sp.HERBIVORE and s.population > 250]
    if not candidates:
        return out
    sp = max(candidates, key=lambda s: s.population)
    # settle at the most suitable nearby site
    cy, cx = int(sp.pos[0]), int(sp.pos[1])
    best, by, bx = 0.0, None, None
    rng = world.rng.stream("emerge")
    for _ in range(20):
        y = int(np.clip(cy + rng.integers(-8, 9), 1, world.height - 2))
        x = int(np.clip(cx + rng.integers(-8, 9), 1, world.width - 2))
        if _cities._too_close(world, y, x):
            continue
        s = _cities.site_suitability(world, y, x)
        if s > best:
            best, by, bx = s, y, x
    if by is None or best < 0.5:
        return out

    cid = world.new_civ_id()
    name = _CIV_NAMES[(cid - 1) % len(_CIV_NAMES)]
    civ = Civilization(id=cid, name=name, origin_species_id=sp.id,
                       founded_tick=world.tick)
    # a late-arising nation still gets a distinct character so the world never reads
    # as one homogeneous people.
    arch = CIV_ARCHETYPES[world.rng.stream("civ_arch").integers(0, len(CIV_ARCHETYPES))]
    apply_archetype(civ, arch)
    world.civilizations[cid] = civ
    city = _cities.found_city(world, civ, by, bx, population=sp.population * 0.25)
    civ.capital_city_id = city.id
    sp.population *= 0.75
    civ.history.append(f"Arose at {city.name} in tick {world.tick}.")
    out.append({"tick": world.tick, "type": "civilization", "civ_id": cid,
                "city_id": city.id, "major": True,
                "title": f"The {name} arose at {city.name}",
                "detail": f"A new {civ.ideology.lower()} nation, the {name}, was founded "
                          f"at {city.name} by the {sp.name} people.",
                "why": {"ideology": civ.ideology, "stance": civ.diplomatic_stance}})
    return out


def seed_initial(world: "_w.WorldState", n: int = 5) -> list[dict]:
    """Genesis of nations. Place ``n`` distinct civilizations on the best, well-spaced
    land so the player opens onto a *plural* world — rival peoples with real, different
    characters — instead of one civ that slowly emerges. Each gets its own founding
    people (a named herbivore lineage), a capital, and a founding history event.
    """
    out: list[dict] = []
    rng = world.rng.stream("seed_civ")
    # choose n distinct archetypes deterministically
    order = list(range(len(CIV_ARCHETYPES)))
    rng.shuffle(order)
    chosen = order[:min(n, len(CIV_ARCHETYPES))]

    # candidate sites: suitable land, ranked, then greedily spaced apart
    land = np.argwhere(world.land_mask)
    if len(land) == 0:
        return out
    sample = land[rng.choice(len(land), size=min(len(land), 1400), replace=False)]
    scored = sorted(((float(_cities.site_suitability(world, int(y), int(x))), int(y), int(x))
                     for y, x in sample), key=lambda t: -t[0])
    capitals: list[tuple[int, int]] = []
    cfg_spacing = int(max(_cities.MIN_CITY_SPACING, world.params.min_city_distance))
    min_spacing = max(cfg_spacing + 6,
                      (world.width + world.height) // (2 * max(2, n)))
    for s, y, x in scored:
        if s < 0.4:
            break
        if any(abs(cy - y) + abs(cx - x) < min_spacing for cy, cx in capitals):
            continue
        capitals.append((y, x))
        if len(capitals) >= len(chosen):
            break
    # relax spacing if the map couldn't host them all
    if len(capitals) < len(chosen):
        for s, y, x in scored:
            if (y, x) in capitals or s < 0.3:
                continue
            if any(abs(cy - y) + abs(cx - x) < cfg_spacing
                   for cy, cx in capitals):
                continue
            capitals.append((y, x))
            if len(capitals) >= len(chosen):
                break

    for idx, (y, x) in enumerate(capitals):
        arch = CIV_ARCHETYPES[chosen[idx]]
        # a distinct founding people for this nation, settled at the capital
        sp = _sp.spawn(world, diet=_sp.HERBIVORE, pos=(float(y), float(x)),
                       population=900.0, genome=_sp._random_genome(rng),
                       name=arch["people"])
        cid = world.new_civ_id()
        civ = Civilization(id=cid, name=arch["name"], origin_species_id=sp.id,
                           founded_tick=world.tick)
        apply_archetype(civ, arch)
        world.civilizations[cid] = civ
        city = _cities.found_city(world, civ, y, x, population=620.0)
        civ.capital_city_id = city.id
        city.history.append(f"Founded as the capital of the {civ.name}.")
        civ.history.append(f"Founded at {city.name}, capital of the {civ.ideology.lower()}.")
        out.append({"tick": world.tick, "type": "civilization", "civ_id": cid,
                    "city_id": city.id, "major": True,
                    "title": f"The {civ.name} is founded at {city.name}",
                    "detail": f"The {arch['people']} establish the {civ.ideology.lower()} "
                              f"of {civ.name} at {city.name}.",
                    "why": {"ideology": civ.ideology, "stance": civ.diplomatic_stance,
                            "traits": civ.cultural_traits}})
    # seed mutual relations from stance compatibility so diplomacy starts with texture
    _seed_relations(world)
    return out


def _seed_relations(world) -> None:
    civs = [c for c in world.civilizations.values() if c.alive]
    for i, a in enumerate(civs):
        for b in civs[i + 1:]:
            # shared traits warm relations; clashing militarism cools them
            shared = len(set(a.cultural_traits) & set(b.cultural_traits))
            clash = (a.military_bias + b.military_bias) / 2
            rel = float(np.clip(0.1 * shared - 0.3 * clash
                                + (0.2 if a.diplomatic_stance == b.diplomatic_stance else 0.0),
                                -0.5, 0.5))
            a.relations[b.id] = b.relations[a.id] = round(rel, 3)


def _diplomacy_and_war(world, civs) -> list[dict]:
    out: list[dict] = []
    p = world.params
    for i, a in enumerate(civs):
        a_cities = a.cities(world)
        if not a_cities:
            continue
        for b in civs[i + 1:]:
            b_cities = b.cities(world)
            if not b_cities:
                continue
            # nearest pair of cities sets the border tension
            pair = min(((ca, cb) for ca in a_cities for cb in b_cities),
                       key=lambda t: abs(t[0].pos[0]-t[1].pos[0]) + abs(t[0].pos[1]-t[1].pos[1]))
            ca, cb = pair
            dist = abs(ca.pos[0]-cb.pos[0]) + abs(ca.pos[1]-cb.pos[1])
            if dist > 40:
                continue
            rel = a.relations.get(b.id, 0.0)
            # contested border erodes relations; distance and trade soothe
            a.relations[b.id] = b.relations[a.id] = max(-1.0, rel - 0.01 * (40 - dist) / 40)
            if a.relations[b.id] < -0.4 and world.rng.chance("declare", 0.02 * p.war_propensity):
                aggressor, victim = (a, b) if a.population_of(world) >= b.population_of(world) else (b, a)
                fc = min(aggressor.cities(world),
                         key=lambda c: abs(c.pos[0]-cb.pos[0]) + abs(c.pos[1]-cb.pos[1]))
                tc = min(victim.cities(world),
                         key=lambda c: abs(c.pos[0]-fc.pos[0]) + abs(c.pos[1]-fc.pos[1]))
                aggressor.war_intents.append({"from_city": fc.id, "to_city": tc.id})
                out.append({"tick": world.tick, "type": "war", "civ_id": aggressor.id,
                            "title": f"{aggressor.name} declares war on {victim.name}",
                            "detail": f"Border tensions near {tc.name} erupted into war.",
                            "why": {"border_distance": dist,
                                    "relations": round(a.relations[b.id], 3),
                                    "war_propensity": round(p.war_propensity, 3),
                                    "resource_pressure": _resource_pressure(ca, cb)}})
    return out


def _ensure_tech(civ: Civilization) -> None:
    if not civ.tech_domains:
        civ.tech_domains = {"agriculture": civ.tech, "metallurgy": civ.tech,
                            "navigation": civ.tech, "governance": civ.tech,
                            "medicine": civ.tech, "warcraft": civ.tech}
    if not hasattr(civ, "tech_milestones") or civ.tech_milestones is None:
        civ.tech_milestones = {}


def _diffuse_technology(world, civs: list[Civilization]) -> list[dict]:
    """Knowledge spreads through real contact: trade proximity, migration/trader units,
    shared borders, and conflict. It is aggregate and bounded; no spreadsheet graph."""
    out: list[dict] = []
    for civ in civs:
        _ensure_tech(civ)
    by_id = {c.id: c for c in civs}

    # City contact: nearby cities exchange ideas faster when educated and commercially
    # connected. This creates diffusion through trade geography without fake routes.
    cities = [c for civ in civs for c in civ.cities(world)]
    for i, a in enumerate(cities):
        ca = by_id.get(a.civ_id)
        if ca is None:
            continue
        for b in cities[i + 1:]:
            if a.civ_id == b.civ_id:
                continue
            dist = abs(a.pos[0] - b.pos[0]) + abs(a.pos[1] - b.pos[1])
            if dist > 58:
                continue
            cb = by_id.get(b.civ_id)
            if cb is None:
                continue
            relation = max(0.0, 0.35 + ca.relations.get(cb.id, 0.0) * 0.35)
            trade = min(1.0, (a.wealth + b.wealth) / 140
                        + (a.buildings.get("market", 0) + b.buildings.get("market", 0)) * 0.04
                        + (a.buildings.get("docks", 0) + b.buildings.get("docks", 0)) * 0.12
                        + (getattr(a, "trade_dependency", 0.0)
                           + getattr(b, "trade_dependency", 0.0)) * 0.12)
            education = max(getattr(a, "education", 0.0), getattr(b, "education", 0.0))
            contact = max(0.0, (1.0 - dist / 58.0)) * (0.35 + trade + education * 0.35) * relation
            if contact > 0.01:
                _diffuse_pair(ca, cb, contact * 0.0012 * world.params.tech_progress)

    # Moving units are visible evidence of contact. Migrants carry broad knowledge;
    # traders/caravans carry navigation/governance/economy-adjacent practices.
    for u in world.units.values():
        if u.origin_city is None or u.dest_city is None:
            continue
        src = world.cities.get(u.origin_city)
        dst = world.cities.get(u.dest_city)
        if not (src and dst) or src.civ_id == dst.civ_id:
            continue
        ca = by_id.get(src.civ_id)
        cb = by_id.get(dst.civ_id)
        if not (ca and cb):
            continue
        strength = min(1.0, max(0.05, getattr(u, "payload", 0.0) / 6000.0))
        rate = strength * (0.0018 if u.kind == "migrant" else 0.001)
        domains = None if u.kind == "migrant" else ("navigation", "governance", "agriculture")
        _diffuse_pair(ca, cb, rate * world.params.tech_progress, domains=domains)

    # Conflict copies tactics and metallurgy, but only at borders where wars can occur.
    for a in civs:
        for bid, rel in list(a.relations.items()):
            b = by_id.get(bid)
            if b is None or rel > -0.25:
                continue
            _diffuse_pair(a, b, 0.0007 * world.params.tech_progress,
                          domains=("warcraft", "metallurgy", "governance"))

    for civ in civs:
        civ.tech = sum(civ.tech_domains.values()) / len(civ.tech_domains)
        out += _tech_milestone_events(world, civ)
    return out


def _diffuse_pair(a: Civilization, b: Civilization, rate: float,
                  domains: tuple[str, ...] | None = None) -> None:
    keys = domains or tuple(a.tech_domains.keys())
    for key in keys:
        av = a.tech_domains.get(key, 0.0)
        bv = b.tech_domains.get(key, 0.0)
        delta = abs(av - bv) * max(0.0, min(0.01, rate))
        if delta <= 0:
            continue
        if av > bv:
            b.tech_domains[key] = bv + delta
        else:
            a.tech_domains[key] = av + delta


def _tech_milestone_events(world, civ: Civilization) -> list[dict]:
    out: list[dict] = []
    cities = civ.cities(world)
    if not cities:
        return out
    city = max(cities, key=lambda c: c.population + getattr(c, "education", 0.0) * 10000)
    for domain, value in civ.tech_domains.items():
        for threshold in (0.25, 0.5, 1.0, 2.0):
            sig = f"{domain}:{threshold}"
            if value < threshold or sig in civ.tech_milestones:
                continue
            civ.tech_milestones[sig] = world.tick
            label = domain.replace("_", " ")
            out.append({"tick": world.tick, "type": "discovery",
                        "civ_id": civ.id, "city_id": city.id,
                        "title": f"{civ.name} advanced in {label}",
                        "detail": f"{city.name}'s schools and contacts pushed {label} beyond {threshold:.2f}.",
                        "why": {"domain": domain, "value": round(value, 3),
                                "education": round(getattr(city, "education", 0.0), 3),
                                "trade_dependency": round(getattr(city, "trade_dependency", 0.0), 3)}})
            break
    return out


def _update_status(world, civ: Civilization, cities) -> None:
    """A coarse life-stage label from real aggregates, used by the UI/renderer and as a
    gate for golden ages and splits."""
    growth = sum(c.growth_rate for c in cities) / len(cities)
    unrest = sum(c.unrest for c in cities) / len(cities)
    if growth > 0.012 and unrest < 0.35:
        civ.status = "rising"
    elif growth < -0.004 or unrest > 0.6:
        civ.status = "declining"
    else:
        civ.status = "stable"


def lifecycle_step(world, civs: list[Civilization]) -> list[dict]:
    """The political life and death of nations beyond simple collapse: cultural golden
    ages, voluntary mergers of friendly weak neighbours, and the fracture of large,
    unstable empires into successor states. Conquest-assimilation lives in units.py;
    revolutions in society/faction.py. Everything here is pressure-driven, never scripted.
    """
    out: list[dict] = []
    p = world.params
    out += _golden_ages(world, civs)
    out += _mass_migrations(world, civs)
    if world.rng.chance("civ_merge", 0.02):
        out += _maybe_merge(world, civs)
    if world.rng.chance("civ_split", 0.02 * p.civ_expansion_drive):
        out += _maybe_split(world, civs)
    return out


def _golden_ages(world, civs) -> list[dict]:
    out: list[dict] = []
    for civ in civs:
        cities = civ.cities(world)
        if len(cities) < 2:
            continue
        culture = sum(c.culture for c in cities)
        wealth = sum(c.wealth for c in cities)
        stability = sum(getattr(c, "civic_stability", 1.0) for c in cities) / len(cities)
        # a golden age: high culture+wealth, broad stability, and a cooldown
        recent = (civ.golden_age_tick is not None
                  and world.tick - civ.golden_age_tick < 900)
        if recent or culture < 240 or wealth < 90 or stability < 0.62:
            continue
        if not world.rng.chance("golden_age", 0.04):
            continue
        civ.golden_age_tick = world.tick
        seat = max(cities, key=lambda c: c.culture)
        seat.culture += 20
        civ.history.append(f"Golden age centred on {seat.name} (tick {world.tick}).")
        out.append({"tick": world.tick, "type": "golden_age", "civ_id": civ.id,
                    "city_id": seat.id, "major": True,
                    "title": f"A golden age dawns for the {civ.name}",
                    "detail": f"Art, learning and trade flourish across the {civ.name}, "
                              f"radiating from {seat.name}.",
                    "why": {"culture": round(culture, 1), "wealth": round(wealth, 1),
                            "stability": round(stability, 3)}})
    return out


def _mass_migrations(world, civs) -> list[dict]:
    """When a civ's cities are broadly under migration pressure, record a visible exodus
    event (the per-tile movement is units.py's job)."""
    out: list[dict] = []
    for civ in civs:
        cities = civ.cities(world)
        if not cities:
            continue
        pressure = sum(getattr(c, "migration_pressure", 0.0) for c in cities) / len(cities)
        if pressure < 0.55 or not world.rng.chance("mass_migration", 0.02):
            continue
        src = max(cities, key=lambda c: getattr(c, "migration_pressure", 0.0))
        world.add_marker("migration", src.pos[0], src.pos[1], ttl=120, label=src.name)
        out.append({"tick": world.tick, "type": "migration", "civ_id": civ.id,
                    "city_id": src.id,
                    "title": f"Exodus from the {civ.name}",
                    "detail": f"Hardship drives families out of {src.name} and its sister "
                              f"cities in search of better land.",
                    "why": {"migration_pressure": round(pressure, 3)}})
    return out


def _maybe_merge(world, civs) -> list[dict]:
    """Two small, friendly, neighbouring civs unite under the stronger one's banner."""
    pairs = []
    for i, a in enumerate(civs):
        for b in civs[i + 1:]:
            rel = a.relations.get(b.id, 0.0)
            if rel < 0.55:
                continue
            ac, bc = a.cities(world), b.cities(world)
            if not ac or not bc or (len(ac) > 3 and len(bc) > 3):
                continue
            pair_dist = min(abs(ca.pos[0]-cb.pos[0]) + abs(ca.pos[1]-cb.pos[1])
                            for ca in ac for cb in bc)
            if pair_dist > 36:
                continue
            pairs.append((rel, a, b))
    if not pairs:
        return []
    pairs.sort(key=lambda t: -t[0])
    _, a, b = pairs[0]
    big, small = (a, b) if a.population_of(world) >= b.population_of(world) else (b, a)
    for cid in list(small.city_ids):
        city = world.cities.get(cid)
        if city is None:
            continue
        city.civ_id = big.id
        if cid not in big.city_ids:
            big.city_ids.append(cid)
    small.city_ids = []
    small.merged_into = big.id
    small.status = "merged"
    big.history.append(f"Absorbed the {small.name} by union (tick {world.tick}).")
    seat = world.cities.get(big.capital_city_id)
    return [{"tick": world.tick, "type": "civilization", "civ_id": big.id,
             "city_id": seat.id if seat else None, "major": True,
             "title": f"The {small.name} unites with the {big.name}",
             "detail": f"Bound by friendship, the {small.name} joined the {big.name} "
                       f"in a single nation.",
             "why": {"merged_civ": small.name, "relation": round(a.relations.get(b.id, 0.0), 3)}}]


def _maybe_split(world, civs) -> list[dict]:
    """A large, unstable civ fractures: its most distant, most disaffected cluster of
    cities secedes as a *successor* state that inherits — then drifts from — its parent's
    character. This is the civ-scale sibling of a city-level revolution."""
    for civ in civs:
        cities = civ.cities(world)
        if len(cities) < 4:
            continue
        unrest = sum(c.unrest for c in cities) / len(cities)
        if unrest < 0.5 and civ.status != "declining":
            continue
        capital = world.cities.get(civ.capital_city_id) or cities[0]
        # the breakaway = disaffected cities far from the capital
        breakaway = [c for c in cities
                     if c.id != capital.id and c.unrest > 0.45
                     and abs(c.pos[0]-capital.pos[0]) + abs(c.pos[1]-capital.pos[1]) > 28]
        if len(breakaway) < 2:
            continue
        cid = world.new_civ_id()
        seat = max(breakaway, key=lambda c: c.population)
        succ = Civilization(id=cid, name=f"{civ.people} Successor State",
                            origin_species_id=civ.origin_species_id,
                            founded_tick=world.tick, parent_civ_id=civ.id)
        # inherit the parent's character, then drift it
        arch_like = {
            "people": civ.people, "color": _shift_color(civ.color), "ideology": civ.ideology,
            "stance": "expansionist", "traits": civ.cultural_traits[:2] + ["independent"],
            "desires": civ.preferred_desires, "economic": civ.economic_bias,
            "military": min(1.0, civ.military_bias + 0.15), "religious": civ.religious_bias,
            "exploration": civ.exploration_bias,
            "axes": {**civ.ideology_axes,
                     "radicalism": min(1.0, civ.ideology_axes.get("radicalism", 0.3) + 0.2)},
        }
        apply_archetype(succ, arch_like)
        succ.name = f"Free {seat.name} ({civ.people})"
        world.civilizations[cid] = succ
        for c in breakaway:
            if c.id in civ.city_ids:
                civ.city_ids.remove(c.id)
            c.civ_id = cid
            c.unrest = max(0.15, c.unrest - 0.25)
            succ.city_ids.append(c.id)
        succ.capital_city_id = seat.id
        succ.history.append(f"Seceded from the {civ.name} at {seat.name} (tick {world.tick}).")
        world.add_marker("revolution", seat.pos[0], seat.pos[1], ttl=140, label=seat.name)
        return [{"tick": world.tick, "type": "schism", "civ_id": cid,
                 "city_id": seat.id, "major": True,
                 "title": f"The {civ.name} fractures",
                 "detail": f"{seat.name} and its neighbours break from the {civ.name} to "
                           f"found the {succ.name}.",
                 "why": {"unrest": round(unrest, 3), "breakaway_cities": len(breakaway),
                         "parent": civ.name}}]
    return []


def _shift_color(hex_color: str) -> str:
    try:
        r = int(hex_color[1:3], 16); g = int(hex_color[3:5], 16); b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        return "#b89cff"
    r = min(255, int(r * 0.7 + 40)); g = min(255, int(g * 0.7 + 40)); b = min(255, int(b * 0.7 + 60))
    return f"#{r:02x}{g:02x}{b:02x}"


def _resource_pressure(a, b) -> dict:
    out = {}
    for key in ("food", "metal", "wood", "luxury"):
        ap = getattr(a, "prices", {}).get(key, 1.0)
        bp = getattr(b, "prices", {}).get(key, 1.0)
        out[key] = round(abs(ap - bp), 3)
    return out
