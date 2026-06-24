"""Cities — where people live. The heart of the civilization layer.

A city is a real, located place with population, a growth rate, food/resource
production drawn from the tiles inside its influence radius, culture, an
infrastructure level, and a specialty determined by its geography. Cities:

  * emerge where geography supports them (food + fresh water + temperate land),
  * grow or starve based on whether local production feeds their population,
  * physically expand their influence radius as they grow,
  * found daughter cities when large and crowded (territorial expansion),
  * raise famine markers the world can *see* when production fails.

Founding is driven from civilization.py (a civ founds its first city; cities found
their own daughters here). Population dynamics, scarcity, and migration pressure all
fall out of the resource grid rather than scripted numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import world as _w
from . import season as _season

FOOD_PER_CAPITA = 0.0013         # food units required per person per tick
DAUGHTER_POP = 9000.0            # population at which a city can spawn a daughter
MIN_CITY_SPACING = 14            # tiles; cities don't found on top of each other
CITY_CAP = 60                    # global cap; keeps the world legible, not sprawling
RESOURCE_KEYS = ("food", "wood", "stone", "metal", "energy", "labor", "luxury", "knowledge")
AGE_GROUP_KEYS = ("children", "working_age", "elders")
CLASS_KEYS = ("poor", "common", "middle", "elite")
PROFESSION_KEYS = (
    "farmers", "laborers", "traders", "craftspeople",
    "soldiers", "scholars", "priests", "miners",
)

_CITY_NAMES = [
    "Aldermoor", "Brighthollow", "Caer Dûn", "Dawnmere", "Eldrath", "Fenwick",
    "Galehaven", "Hollowfast", "Ironvale", "Jadeport", "Kessrid", "Lowmarsh",
    "Mournhold", "Norwick", "Osterfell", "Pyrehall", "Quillhaven", "Ravenstead",
    "Solmere", "Thorngate", "Umbral Reach", "Velenmoor", "Westcrag", "Yarrow",
    "Zephyrholt", "Ashford", "Briarcliff", "Crestfall", "Duskwater", "Emberton",
]


@dataclass
class Building:
    id: str
    kind: str
    city_id: int
    district: str
    owner_id: int | None = None
    workers: list[int] = field(default_factory=list)
    inventory: dict[str, float] = field(default_factory=dict)
    production: dict[str, float] = field(default_factory=dict)
    wealth: float = 0.0
    condition: float = 1.0
    age: int = 0
    history: list[str] = field(default_factory=list)
    abandoned: bool = False


@dataclass
class City:
    id: int
    name: str
    civ_id: int
    pos: tuple[int, int]              # (y, x) grid tile
    population: float
    founded_tick: int

    food_production: float = 0.0     # last tick's harvest
    culture: float = 0.0             # accumulates; seeds specialties + influence
    infrastructure: float = 1.0      # 1..10, boosts production + defense
    influence_radius: float = 4.0    # tiles of claimed territory
    growth_rate: float = 0.0         # last tick's fractional change (for display)
    wealth: float = 0.0              # trade income buffer
    stocks: dict[str, float] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    resource_production: dict[str, float] = field(default_factory=dict)
    resource_consumption: dict[str, float] = field(default_factory=dict)
    shortages: dict[str, float] = field(default_factory=dict)
    surplus: dict[str, float] = field(default_factory=dict)
    demand_pressure: float = 0.0
    trade_dependency: float = 0.0
    famine_risk: float = 0.0
    war_readiness: float = 0.0
    civic_stability: float = 1.0
    demographics: dict[str, float] = field(default_factory=dict)
    class_mix: dict[str, float] = field(default_factory=dict)
    professions: dict[str, float] = field(default_factory=dict)
    education: float = 0.0
    urbanization: float = 0.0
    fertility_rate: float = 0.0
    mortality_rate: float = 0.0
    migration_pressure: float = 0.0
    heritage: float = 0.0
    trauma: float = 0.0
    buildings: dict[str, int] = field(default_factory=dict)
    building_entities: dict[str, Building] = field(default_factory=dict)
    economic_health: float = 1.0
    damage: float = 0.0
    last_crisis_tick: int = -9999
    specialty: str = "Settlement"
    famine: int = 0                  # ticks remaining of famine state
    plague: int = 0
    unrest: float = 0.0
    history: list[str] = field(default_factory=list)
    abandoned_tick: int | None = None

    @property
    def alive(self) -> bool:
        return self.abandoned_tick is None and self.population >= 1

    @property
    def tier(self) -> str:
        p = self.population
        if p < 800:   return "hamlet"
        if p < 3000:  return "village"
        if p < 9000:  return "town"
        if p < 25000: return "city"
        return "metropolis"


def found_city(world, civ, y: int, x: int, population: float,
               name: str | None = None) -> City:
    cid = world.new_city_id()
    city = City(
        id=cid,
        name=name or _CITY_NAMES[(cid - 1) % len(_CITY_NAMES)],
        civ_id=civ.id,
        pos=(int(y), int(x)),
        population=float(population),
        founded_tick=world.tick,
    )
    _init_economy(world, city)
    _update_specialty(world, city)
    world.cities[cid] = city
    civ.city_ids.append(cid)
    world.add_marker("founded", y, x, ttl=60, label=city.name)
    return city


def _init_economy(world, city: City) -> None:
    y, x = city.pos
    city.stocks = {
        "food": max(10.0, float(world.food[_region(world, y, x, 4)].sum())),
        "wood": 20.0,
        "stone": 12.0 + max(0, world.elevation[y, x]) * 20,
        "metal": 8.0 + float(world.minerals[_region(world, y, x, 4)].sum()) * 20,
        "energy": float(world.energy[_region(world, y, x, 4)].sum()) * 10,
        "luxury": 2.0 + city.culture * 0.1,
        "labor": max(1.0, city.population * 0.35),
        "knowledge": city.culture * 0.2,
    }
    city.prices = {k: 1.0 for k in city.stocks}
    city.resource_production = {k: 0.0 for k in RESOURCE_KEYS}
    city.resource_consumption = {k: 0.0 for k in RESOURCE_KEYS}
    city.shortages = {k: 0.0 for k in RESOURCE_KEYS}
    city.surplus = {k: 0.0 for k in RESOURCE_KEYS}
    city.demand_pressure = 0.0
    city.trade_dependency = 0.0
    city.famine_risk = 0.0
    city.war_readiness = 0.0
    city.civic_stability = 1.0
    city.demographics = {k: 0.0 for k in AGE_GROUP_KEYS}
    city.class_mix = {k: 0.0 for k in CLASS_KEYS}
    city.professions = {k: 0.0 for k in PROFESSION_KEYS}
    city.education = 0.0
    city.urbanization = 0.0
    city.fertility_rate = 0.0
    city.mortality_rate = 0.0
    city.migration_pressure = 0.0
    city.heritage = 0.0
    city.trauma = 0.0
    city.buildings = {"homes": max(1, int(city.population / 6)),
                      "farms": 2, "market": 1, "tavern": 1,
                      "workshops": 1, "docks": 0, "temples": 0,
                      "archives": 0, "barracks": 0, "slums": 0,
                      "mines": 0, "noble_district": 0}
    _sync_building_entities(world, city)


def _region(world, cy: int, cx: int, r: int):
    h, w = world.height, world.width
    r = int(r)
    y0, y1 = max(0, cy - r), min(h, cy + r + 1)
    x0, x1 = max(0, cx - r), min(w, cx + r + 1)
    return (slice(y0, y1), slice(x0, x1))


def site_suitability(world, y: int, x: int) -> float:
    """How good a place this is to live: food + fresh water + temperate + low."""
    if not world.land_mask[y, x]:
        return 0.0
    reg = _region(world, y, x, 3)
    food = float(world.food[reg].mean())
    fresh = 0.3 if float(world.water[reg].max()) > 0.2 else 0.0   # river/lake bonus
    coast = 0.2 if _is_coastal(world, y, x) else 0.0
    temp = float(world.temperature[y, x])
    temp_fit = np.exp(-((temp - 18) ** 2) / 400)                  # ~18C ideal
    elev_pen = max(0.0, world.elevation[y, x] - 0.5)              # mountains hurt
    return max(0.0, food + fresh + coast + 0.4 * temp_fit - elev_pen)


def _is_coastal(world, y: int, x: int) -> bool:
    reg = _region(world, y, x, 1)
    return bool((world.biome[reg] == _w.BIOME["ocean"]).any())


def _update_specialty(world, city: City) -> None:
    y, x = city.pos
    reg = _region(world, y, x, int(city.influence_radius))
    minerals = float(world.minerals[reg].mean())
    food = float(world.food[reg].mean())
    coastal = _is_coastal(world, y, x)
    if city.culture > 40 and city.infrastructure > 4:
        city.specialty = "Cultural Center"
    elif coastal and city.wealth > 30:
        city.specialty = "Trade Port"
    elif minerals > 0.4:
        city.specialty = "Mining Town"
    elif food > 0.55:
        city.specialty = "Breadbasket"
    elif city.infrastructure > 5:
        city.specialty = "Fortress City"
    else:
        city.specialty = "Settlement"


def _too_close(world, y: int, x: int) -> bool:
    for c in world.cities.values():
        if not c.alive:
            continue
        cy, cx = c.pos
        if abs(cy - y) + abs(cx - x) < MIN_CITY_SPACING:
            return True
    return False


def step(world: "_w.WorldState") -> list[dict]:
    out: list[dict] = []
    p = world.params
    for city in list(world.cities.values()):
        if not city.alive:
            continue
        _ensure_economy_fields(city)
        _ensure_demographic_fields(city)
        cy, cx = city.pos
        r = int(city.influence_radius)
        reg = _region(world, cy, cx, r)

        # --- production: harvest local food, modulated by infrastructure + season ---
        local_food = float(world.food[reg].sum())
        knowledge_bonus = min(0.22, city.stocks.get("knowledge", 0.0) / 420.0)
        labor_drag = min(0.18, city.shortages.get("labor", 0.0) * 0.16)
        supply = (local_food * (0.4 + 0.12 * city.infrastructure) * p.plant_growth
                  * (1.0 + knowledge_bonus - labor_drag)
                  * _season.food_factor(world.tick))
        demand = city.population * FOOD_PER_CAPITA
        city.food_production = supply
        out += _economy_step(world, city, reg, supply, demand)
        ratio = supply / demand if demand > 0 else 2.0
        _demographics_step(world, city, ratio)
        memory = _historical_memory_pressure(world, city)

        # harvesting depletes the land a little (resources.step regrows it)
        if local_food > 0:
            take = min(0.18, demand / max(local_food, 1e-6) * 0.18)
            world.food[reg] *= (1.0 - take)

        # --- growth / starvation ---
        base = 0.035 * (ratio - 1.0)
        base += 0.003 * p.tech_progress                    # slow baseline progress
        base += 0.006 * max(0.0, city.economic_health - 0.65)
        base -= 0.02 * city.unrest
        base -= 0.015 * city.damage
        base -= 0.02 * city.demand_pressure
        base += city.fertility_rate - city.mortality_rate
        base -= 0.01 * city.migration_pressure
        base -= 0.006 * memory["trauma"]
        base += 0.003 * memory["heritage"]
        city.growth_rate = float(np.clip(base, -0.08, 0.035))
        city.population = max(0.0, city.population * (1 + city.growth_rate))

        # --- famine state (visible) ---
        if ratio < 0.85:
            if city.famine == 0:
                world.add_marker("famine", cy, cx, ttl=120, label=city.name)
                if city.population > 4000:   # only notable famines reach the chronicle
                    out.append(_ev(world, "famine", f"Famine grips {city.name}",
                                   f"{city.name} cannot feed its {int(city.population)} people.",
                                   why={"food_supply": round(supply, 2),
                                        "food_demand": round(demand, 2),
                                        "food_price": city.prices.get("food", 1.0),
                                        "cause": "local harvest could not meet demand"}))
            city.famine = 60
            city.unrest = min(1.0, city.unrest + 0.05)
        else:
            city.famine = max(0, city.famine - 1)
            city.unrest = max(0.0, city.unrest - 0.02)
        if city.plague > 0:
            city.plague -= 1
            city.population *= 0.992

        # --- culture, infrastructure, influence grow with prosperity ---
        if ratio > 1.1:
            city.culture += 0.05 * (1 + city.population / 10000)
            city.infrastructure = min(10.0, city.infrastructure
                                      + 0.012 * p.tech_progress * (1 + city.wealth / 50))
            city.wealth += 0.2 * (ratio - 1.0)
        if city.surplus.get("knowledge", 0.0) > 0.2:
            city.culture += 0.02 * city.surplus["knowledge"]
            city.infrastructure = min(10.0, city.infrastructure + 0.003 * p.tech_progress)
        if city.surplus.get("luxury", 0.0) > 0.25:
            city.unrest = max(0.0, city.unrest - 0.006 * city.surplus["luxury"])
        if memory["heritage"] > 0.08 and world.tick % 12 == 0:
            city.culture += 0.01 * memory["heritage"]
        if memory["trauma"] > 0.08 and world.tick % 12 == 0:
            city.unrest = min(1.0, city.unrest + 0.0015 * memory["trauma"])
        if city.shortages.get("labor", 0.0) > 0.55:
            city.infrastructure = max(0.5, city.infrastructure - 0.002 * city.shortages["labor"])
        city.influence_radius = float(np.clip(
            3.0 + 1.6 * np.sqrt(city.population / 1000.0), 3.0, 22.0))

        if world.tick % 50 == 0:
            _update_specialty(world, city)
            _update_buildings(world, city, reg)

        # --- expansion: found a daughter city (respecting the global cap) ---
        n_live = sum(1 for c in world.cities.values() if c.alive)
        if (city.population > DAUGHTER_POP and city.wealth > 20
                and n_live < CITY_CAP
                and world.rng.chance("daughter", 0.02 * p.civ_expansion_drive)):
            ev = _try_daughter(world, city)
            if ev:
                out.append(ev)

        if city.population < 1:
            city.abandoned_tick = world.tick
            out.append(_ev(world, "collapse", f"{city.name} was abandoned",
                           f"The last of {city.name} drifted away."))
    return out


def _economy_step(world, city: City, reg, supply: float, demand: float) -> list[dict]:
    out: list[dict] = []
    if not city.stocks:
        _init_economy(world, city)
    else:
        _ensure_economy_fields(city)
    minerals = float(world.minerals[reg].mean())
    energy = float(world.energy[reg].mean())
    wood_gain = 0.2 + float((world.biome[reg] == _w.BIOME["forest"]).mean()) * 3
    stone_gain = 0.1 + float((world.biome[reg] == _w.BIOME["mountain"]).mean()) * 2.5
    production = {
        "food": supply,
        "wood": wood_gain,
        "stone": stone_gain,
        "metal": minerals * 0.4,
        "energy": energy * 0.2,
        "luxury": city.culture * 0.002 + city.wealth * 0.005,
        "labor": city.population * (0.28 + 0.12 * (1 - city.unrest)),
        "knowledge": city.culture * 0.006 + city.infrastructure * 0.003,
    }
    consumption = {
        "food": demand,
        "wood": city.population * 0.0002 + city.damage * 0.04,
        "stone": city.infrastructure * 0.03 + city.damage * 0.06,
        "metal": city.infrastructure * 0.02 + city.unrest * 0.035,
        "energy": city.population * 0.00005 + city.infrastructure * 0.006,
        "luxury": 0.006 * max(0.0, city.population / 1000.0 - 1.0),
        "labor": max(1.0, city.population * (0.30 + city.damage * 0.08)),
        "knowledge": 0.006 * city.unrest + 0.002 * city.damage,
    }
    for k in RESOURCE_KEYS:
        if k == "labor":
            city.stocks[k] = max(1.0, production[k])
        else:
            city.stocks[k] = max(0.0, city.stocks.get(k, 0.0)
                                 + production[k] - consumption[k])

    desired = _resource_targets(city, demand)
    for k, want in desired.items():
        stock = city.stocks.get(k, 0.0)
        scarcity = max(0.25, min(4.0, want / max(1.0, stock)))
        city.prices[k] = round(0.9 * city.prices.get(k, 1.0) + 0.1 * scarcity, 3)

    city.resource_production = {k: round(float(production.get(k, 0.0)), 4) for k in RESOURCE_KEYS}
    city.resource_consumption = {k: round(float(consumption.get(k, 0.0)), 4) for k in RESOURCE_KEYS}
    city.shortages = {}
    city.surplus = {}
    for k, want in desired.items():
        stock = city.stocks.get(k, 0.0)
        city.shortages[k] = round(max(0.0, min(1.0, (want - stock) / max(1.0, want))), 4)
        city.surplus[k] = round(max(0.0, min(2.0, (stock - want) / max(1.0, want))), 4)

    weights = {"food": 1.35, "labor": 1.05, "wood": 0.75, "stone": 0.65,
               "metal": 0.9, "energy": 0.75, "luxury": 0.45, "knowledge": 0.55}
    demand_pressure = sum(city.shortages[k] * weights[k] for k in RESOURCE_KEYS) / sum(weights.values())
    avg_price_pressure = sum(city.prices.values()) / max(1, len(city.prices))
    local_supply = production["food"] + production["wood"] + production["stone"] \
        + production["metal"] + production["energy"] + production["knowledge"] * 0.6
    total_need = sum(desired.values())
    city.demand_pressure = round(float(max(0.0, min(1.0, demand_pressure))), 4)
    city.trade_dependency = round(float(max(0.0, min(1.0, 1.0 - local_supply / max(1.0, total_need * 0.12)
                                                   + city.demand_pressure * 0.35))), 4)
    city.famine_risk = round(float(max(0.0, min(1.0, city.shortages.get("food", 0.0) * 0.72
                                               + max(0.0, city.prices.get("food", 1.0) - 1.1) * 0.18))), 4)
    city.war_readiness = round(float(max(0.0, min(1.0,
        city.infrastructure / 10 * 0.32
        + (1.0 - city.shortages.get("food", 0.0)) * 0.22
        + (1.0 - city.shortages.get("metal", 0.0)) * 0.2
        + (1.0 - city.shortages.get("labor", 0.0)) * 0.16
        + city.stocks.get("knowledge", 0.0) / 260 * 0.1))), 4)
    city.civic_stability = round(float(max(0.0, min(1.0,
        city.economic_health * 0.42 + (1.0 - city.unrest) * 0.34
        + (1.0 - city.famine_risk) * 0.16 + (0.08 if city.plague == 0 else 0.0)))), 4)
    city.economic_health = max(0.0, min(1.0,
        1.15 - 0.18 * avg_price_pressure - 0.45 * city.unrest
        + 0.025 * city.infrastructure + 0.01 * city.wealth - 0.25 * city.demand_pressure))
    city.civic_stability = round(float(max(0.0, min(1.0,
        city.economic_health * 0.42 + (1.0 - city.unrest) * 0.34
        + (1.0 - city.famine_risk) * 0.16 + (0.08 if city.plague == 0 else 0.0)))), 4)
    city.unrest = min(1.0, city.unrest + city.demand_pressure * 0.006
                      + city.shortages.get("food", 0.0) * 0.006
                      - city.surplus.get("luxury", 0.0) * 0.004)
    crash = city.prices.get("food", 1) > 2.7 or city.prices.get("labor", 1) > 2.3 \
        or city.economic_health < 0.18
    if crash:
        city.unrest = min(1.0, city.unrest + 0.015)
        if world.tick - city.last_crisis_tick > 120:
            city.last_crisis_tick = world.tick
            out.append(_ev(world, "economy", f"Economic crisis in {city.name}",
                           f"Prices surged and confidence collapsed in {city.name}.",
                           city_id=city.id,
                           why={"food_price": city.prices.get("food", 1.0),
                                "labor_price": city.prices.get("labor", 1.0),
                                "economic_health": round(city.economic_health, 3),
                                "scarce_goods": sorted(city.prices, key=city.prices.get,
                                                       reverse=True)[:3]}))
    return out


def _ensure_economy_fields(city: City) -> None:
    """Old autosaves may contain City objects created before resource-flow fields."""
    for name in ("resource_production", "resource_consumption", "shortages", "surplus"):
        if not hasattr(city, name) or getattr(city, name) is None:
            setattr(city, name, {k: 0.0 for k in RESOURCE_KEYS})
        else:
            data = getattr(city, name)
            for key in RESOURCE_KEYS:
                data.setdefault(key, 0.0)
    for name, value in (
        ("demand_pressure", 0.0), ("trade_dependency", 0.0),
        ("famine_risk", 0.0), ("war_readiness", 0.0),
        ("civic_stability", 1.0),
    ):
        if not hasattr(city, name):
            setattr(city, name, value)


def _ensure_demographic_fields(city: City) -> None:
    """Old autosaves may lack compact city sociology fields."""
    for name, keys in (
        ("demographics", AGE_GROUP_KEYS),
        ("class_mix", CLASS_KEYS),
        ("professions", PROFESSION_KEYS),
    ):
        if not hasattr(city, name) or getattr(city, name) is None:
            setattr(city, name, {k: 0.0 for k in keys})
        else:
            data = getattr(city, name)
            for key in keys:
                data.setdefault(key, 0.0)
    for name, value in (
        ("education", 0.0), ("urbanization", 0.0),
        ("fertility_rate", 0.0), ("mortality_rate", 0.0),
        ("migration_pressure", 0.0),
        ("heritage", 0.0), ("trauma", 0.0),
    ):
        if not hasattr(city, name):
            setattr(city, name, value)


def _demographics_step(world, city: City, food_ratio: float) -> None:
    """Aggregate demographics derived from real city state.

    These are not individuals. They are compact rates/mixes that let policy,
    migration, visuals, and city inspection show why a place feels young, old,
    learned, stratified, unstable, or labor-starved without full-pop payloads.
    """
    buildings = getattr(city, "buildings", {})
    shortages = getattr(city, "shortages", {})
    surplus = getattr(city, "surplus", {})
    pop_factor = float(np.clip(city.population / 30000.0, 0.0, 1.0))
    wealth = float(np.clip(city.wealth / 100.0, 0.0, 1.0))
    infra = float(np.clip(city.infrastructure / 10.0, 0.0, 1.0))
    stability = float(np.clip(getattr(city, "civic_stability", 1.0), 0.0, 1.0))
    scarcity = float(np.clip(1.0 - min(1.4, food_ratio) / 1.4, 0.0, 1.0))
    plague = 1.0 if city.plague > 0 else 0.0
    age = max(0, world.tick - city.founded_tick)
    old_city = float(np.clip(age / 1800.0, 0.0, 1.0))

    city.education = round(float(np.clip(
        city.culture / 180.0
        + city.stocks.get("knowledge", 0.0) / 260.0
        + buildings.get("archives", 0) * 0.08
        + infra * 0.16
        - shortages.get("knowledge", 0.0) * 0.22,
        0.0, 1.0)), 4)
    city.urbanization = round(float(np.clip(
        pop_factor * 0.42 + infra * 0.24 + buildings.get("market", 0) * 0.035
        + buildings.get("workshops", 0) * 0.025 + buildings.get("noble_district", 0) * 0.1
        - buildings.get("farms", 0) * 0.004,
        0.0, 1.0)), 4)

    children = np.clip(0.28 + (1.0 - city.urbanization) * 0.08
                       - scarcity * 0.07 - plague * 0.04 - city.education * 0.04,
                       0.12, 0.42)
    elders = np.clip(0.08 + old_city * 0.08 + wealth * 0.05 + stability * 0.03
                     - plague * 0.04 - scarcity * 0.03,
                     0.04, 0.22)
    working = max(0.45, 1.0 - children - elders)
    city.demographics = _normalize_mix({
        "children": float(children),
        "working_age": float(working),
        "elders": float(elders),
    }, AGE_GROUP_KEYS)

    elite = np.clip(0.03 + wealth * 0.07 + buildings.get("noble_district", 0) * 0.035
                    + surplus.get("luxury", 0.0) * 0.02, 0.01, 0.16)
    middle = np.clip(0.16 + infra * 0.12 + city.education * 0.09
                     + city.economic_health * 0.08 - city.unrest * 0.08, 0.08, 0.38)
    poor = np.clip(0.22 + city.unrest * 0.18 + scarcity * 0.16
                   + buildings.get("slums", 0) / max(8.0, sum(buildings.values())) * 0.4
                   - wealth * 0.1, 0.08, 0.62)
    common = max(0.2, 1.0 - elite - middle - poor)
    city.class_mix = _normalize_mix({
        "poor": float(poor), "common": float(common),
        "middle": float(middle), "elite": float(elite),
    }, CLASS_KEYS)

    farm_score = buildings.get("farms", 0) * 1.1 + max(0.0, city.food_production) / 18.0
    labor_score = city.population / 1500.0 + buildings.get("slums", 0) * 0.7
    trade_score = buildings.get("market", 0) * 2.2 + buildings.get("docks", 0) * 2.8 + city.wealth / 18.0
    craft_score = buildings.get("workshops", 0) * 2.1 + city.infrastructure * 0.5
    soldier_score = buildings.get("barracks", 0) * 2.8 + getattr(city, "war_readiness", 0.0) * 3.0
    scholar_score = buildings.get("archives", 0) * 3.0 + city.education * 4.0
    priest_score = buildings.get("temples", 0) * 3.2 + city.culture / 45.0
    miner_score = buildings.get("mines", 0) * 2.8 + city.stocks.get("metal", 0.0) / 55.0
    city.professions = _normalize_mix({
        "farmers": farm_score, "laborers": labor_score,
        "traders": trade_score, "craftspeople": craft_score,
        "soldiers": soldier_score, "scholars": scholar_score,
        "priests": priest_score, "miners": miner_score,
    }, PROFESSION_KEYS)

    city.fertility_rate = round(float(np.clip(
        0.0045 * city.demographics["working_age"] * (0.65 + stability * 0.55)
        * (1.0 - scarcity * 0.65) * (1.0 - city.urbanization * 0.22),
        0.0, 0.006)), 5)
    city.mortality_rate = round(float(np.clip(
        0.0012 + scarcity * 0.006 + city.unrest * 0.0025
        + city.damage * 0.0035 + plague * 0.0055,
        0.0005, 0.018)), 5)
    city.migration_pressure = round(float(np.clip(
        scarcity * 0.34 + city.unrest * 0.22
        + getattr(city, "trade_dependency", 0.0) * 0.18
        + shortages.get("labor", 0.0) * 0.08
        + (1.0 - getattr(city, "economic_health", 1.0)) * 0.2
        - wealth * 0.08 - city.education * 0.04,
        0.0, 1.0)), 4)


def _historical_memory_pressure(world, city: City) -> dict[str, float]:
    trauma_kinds = {"battlefield", "ruin", "famine_marker", "plague_marker",
                    "market_crisis", "schism_site"}
    heritage_kinds = {"foundation", "shrine", "monument", "discovery_site",
                      "migration_waypoint"}
    trauma = 0.0
    heritage = 0.0
    cy, cx = city.pos
    for site in getattr(world, "historical_sites", [])[-500:]:
        sy = float(site.get("y", -9999))
        sx = float(site.get("x", -9999))
        d = abs(cy - sy) + abs(cx - sx)
        if d > max(8.0, city.influence_radius * 2.2):
            continue
        age = max(0, world.tick - int(site.get("tick", world.tick)))
        memory = float(site.get("intensity", 0.35)) * max(0.18, 1.0 - age / 7200.0)
        weight = max(0.0, 1.0 - d / max(8.0, city.influence_radius * 2.2))
        if site.get("kind") in trauma_kinds:
            trauma += memory * weight
        if site.get("kind") in heritage_kinds:
            heritage += memory * weight
    city.trauma = round(float(np.clip(trauma, 0.0, 1.0)), 4)
    city.heritage = round(float(np.clip(heritage, 0.0, 1.0)), 4)
    return {"trauma": city.trauma, "heritage": city.heritage}


def _normalize_mix(values: dict[str, float], keys: tuple[str, ...]) -> dict[str, float]:
    cleaned = {k: max(0.0, float(values.get(k, 0.0))) for k in keys}
    total = sum(cleaned.values())
    if total <= 0:
        return {k: round(1.0 / len(keys), 4) for k in keys}
    return {k: round(cleaned[k] / total, 4) for k in keys}


def _resource_targets(city: City, demand: float) -> dict[str, float]:
    """Desired buffers. These are deliberately compact: enough pressure for trade,
    migration, war-readiness, and visuals without turning AEON into a spreadsheet."""
    pop = max(1.0, city.population)
    return {
        "food": max(1.0, demand * 20),
        "wood": 38 + pop / 650,
        "stone": 34 + city.infrastructure * 2.2,
        "metal": 24 + city.infrastructure * 1.8 + city.buildings.get("barracks", 0) * 5,
        "energy": 14 + city.infrastructure * 1.1 + city.buildings.get("workshops", 0) * 1.4,
        "luxury": 10 + pop / 3200 + max(0.0, city.wealth) / 20,
        "labor": max(1.0, pop * 0.35),
        "knowledge": 18 + city.infrastructure * 1.5 + city.culture / 10,
    }


def _update_buildings(world, city: City, reg) -> None:
    if not city.buildings:
        _init_economy(world, city)
    city.buildings["homes"] = max(1, int(city.population / 6))
    city.buildings["slums"] = max(0, int(city.population * city.unrest / 700))
    city.buildings["farms"] = max(1, int(city.food_production / 18))
    city.buildings["market"] = max(1, int(city.wealth / 25) + 1)
    city.buildings["workshops"] = max(1, int((city.stocks.get("wood", 0) + city.stocks.get("metal", 0)) / 80))
    city.buildings["docks"] = 1 if _is_coastal(world, *city.pos) else 0
    city.buildings["mines"] = max(0, int(float(world.minerals[reg].mean()) * 5))
    city.buildings["temples"] = max(0, int(city.culture / 45))
    city.buildings["archives"] = max(0, int(city.stocks.get("knowledge", 0) / 45))
    city.buildings["barracks"] = max(0, int(city.infrastructure / 4))
    city.buildings["noble_district"] = 1 if city.wealth > 60 and city.culture > 25 else 0
    _sync_building_entities(world, city)


def _sync_building_entities(world, city: City) -> None:
    """Keep stable building records derived from the city's real aggregate state.

    Homes/farms can become statistically large, so each entity is a durable rendered
    building parcel with a capacity implied by the current count. The renderer can
    still show thousands of instances, but Python does not need one object per hut.
    """
    if not hasattr(city, "building_entities") or city.building_entities is None:
        city.building_entities = {}
    desired: dict[str, int] = {}
    for kind, count in city.buildings.items():
        cap = 220 if kind in ("homes", "farms") else 70 if kind == "slums" else 45
        desired[kind] = max(0, min(int(count), cap))

    keep: set[str] = set()
    for kind, count in desired.items():
        for i in range(count):
            bid = f"c{city.id}:{kind}:{i}"
            keep.add(bid)
            b = city.building_entities.get(bid)
            if b is None:
                b = Building(
                    id=bid, kind=kind, city_id=city.id,
                    district=_district_for(kind),
                    age=max(0, world.tick - city.founded_tick),
                    history=[f"Built in {city.name} at tick {world.tick}."],
                )
                city.building_entities[bid] = b
            _update_building_state(world, city, b)
    for bid, b in list(city.building_entities.items()):
        if bid not in keep:
            b.abandoned = True
            b.condition = max(0.05, b.condition - 0.02)


def _district_for(kind: str) -> str:
    if kind in ("market", "tavern", "warehouses"):
        return "market"
    if kind in ("docks",):
        return "waterfront"
    if kind in ("temples",):
        return "sacred"
    if kind in ("archives",):
        return "scholarly"
    if kind in ("barracks",):
        return "military"
    if kind in ("noble_district",):
        return "noble"
    if kind in ("workshops", "mines"):
        return "industrial"
    if kind in ("farms",):
        return "farmland"
    if kind in ("slums",):
        return "poor"
    return "residential"


def _update_building_state(world, city: City, b: Building) -> None:
    pressure = max(city.unrest, city.damage, 1.0 - city.economic_health)
    b.condition = max(0.05, min(1.0, 1.0 - 0.45 * pressure))
    b.wealth = max(0.0, min(1.0, city.wealth / 90))
    b.age = max(0, world.tick - city.founded_tick)
    b.inventory = _building_inventory(city, b.kind)
    b.production = _building_production(city, b.kind)
    b.abandoned = city.famine > 0 and b.kind in ("farms", "slums") and city.unrest > 0.6


def _building_inventory(city: City, kind: str) -> dict[str, float]:
    keys = {
        "homes": ("food", "wood", "luxury"),
        "slums": ("food", "labor"),
        "farms": ("food", "wood"),
        "market": ("food", "luxury", "metal"),
        "tavern": ("food", "luxury"),
        "workshops": ("wood", "metal", "energy"),
        "docks": ("food", "wood", "luxury"),
        "temples": ("stone", "luxury", "knowledge"),
        "archives": ("knowledge", "luxury"),
        "barracks": ("metal", "food"),
        "mines": ("metal", "stone", "energy"),
        "noble_district": ("luxury", "knowledge"),
    }.get(kind, ("food",))
    n = max(1, city.buildings.get(kind, 1))
    return {k: round(city.stocks.get(k, 0.0) / n, 2) for k in keys}


def _building_production(city: City, kind: str) -> dict[str, float]:
    if kind == "farms":
        return {"food": round(city.food_production / max(1, city.buildings.get(kind, 1)), 2)}
    if kind == "mines":
        return {"metal": round(city.stocks.get("metal", 0.0) * 0.01, 2),
                "stone": round(city.stocks.get("stone", 0.0) * 0.01, 2)}
    if kind == "workshops":
        return {"goods": round((city.stocks.get("wood", 0.0) + city.stocks.get("metal", 0.0)) * 0.006, 2)}
    if kind == "market":
        return {"trade": round(city.wealth * 0.03, 2)}
    if kind == "temples":
        return {"faith": round(city.culture * 0.02, 2)}
    if kind == "archives":
        return {"knowledge": round(city.stocks.get("knowledge", 0.0) * 0.02, 2)}
    if kind == "docks":
        return {"trade": round(city.wealth * 0.025, 2)}
    if kind == "barracks":
        return {"security": round(city.infrastructure * 0.05, 2)}
    return {}


def _try_daughter(world, parent: City):
    """Scout the parent's surroundings for the best unclaimed site and settle it."""
    cy, cx = parent.pos
    rng = world.rng.stream("daughter")
    best, by, bx = 0.0, None, None
    for _ in range(14):
        dist = int(rng.integers(MIN_CITY_SPACING, MIN_CITY_SPACING + 14))
        ang = rng.random() * 2 * np.pi
        y = int(np.clip(cy + dist * np.sin(ang), 1, world.height - 2))
        x = int(np.clip(cx + dist * np.cos(ang), 1, world.width - 2))
        if _too_close(world, y, x):
            continue
        s = site_suitability(world, y, x)
        if s > best:
            best, by, bx = s, y, x
    if by is None or best < 0.4:
        return None
    civ = world.civilizations.get(parent.civ_id)
    if civ is None or not civ.alive:
        return None
    settlers = parent.population * 0.15
    parent.population -= settlers
    child = found_city(world, civ, by, bx, settlers)
    child.history.append(f"Founded by settlers from {parent.name}.")
    return _ev(world, "settlement", f"{civ.name} founded {child.name}",
               f"Settlers from {parent.name} built {child.name}.",
               civ_id=civ.id, city_id=child.id)


def _ev(world, type_, title, detail, **extra):
    return {"tick": world.tick, "type": type_, "title": title,
            "detail": detail, **extra}
