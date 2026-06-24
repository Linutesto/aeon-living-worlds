"""Emergent cultures: traditions and style as accumulated city memory."""

from __future__ import annotations

from dataclasses import dataclass, field


SYMBOLS = ["river-knot", "sun-disc", "iron spiral", "green hand", "white gate",
           "blue sail", "red banner", "stone eye"]
VALUES = ["hospitality", "honor", "trade", "learning", "piety", "discipline",
          "craft", "kinship", "freedom"]
RITUALS = ["market fast", "river blessing", "ancestor supper", "first-harvest",
           "oath night", "lantern march", "founders' song"]
STYLES = ["timber courts", "stone terraces", "painted roofs", "dockside halls",
          "walled compounds", "garden shrines"]


@dataclass
class Culture:
    id: int
    name: str
    origin_city: int
    origin_city_name: str
    founded_tick: int
    values: list[str]
    rituals: list[str]
    taboos: list[str]
    symbols: list[str]
    architecture: str
    cities: dict[int, float] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    alive: bool = True


def step(society, world, population) -> list[dict]:
    out = []
    out += _maybe_found(society, world, population)
    _spread(society, world)
    return out


def _maybe_found(society, world, population) -> list[dict]:
    if len(society.cultures) > 80 or not world.rng.chance("culture_found", 0.05):
        return []
    cities = [c for c in world.cities.values() if c.alive and c.culture > 4]
    if not cities:
        return []
    city = max(cities, key=lambda c: c.culture + c.wealth * 0.2)
    if any(c.origin_city == city.id for c in society.cultures.values()):
        return []
    rng = world.rng.stream("culture")
    cid = society.nid()
    name = f"{city.name} Custom"
    values = _pickn(rng, VALUES, 3)
    if city.specialty == "Trade Port":
        values.append("trade")
    if city.specialty == "Cultural Center":
        values.append("learning")
    culture = Culture(
        id=cid, name=name, origin_city=city.id, origin_city_name=city.name,
        founded_tick=world.tick, values=list(dict.fromkeys(values)),
        rituals=_pickn(rng, RITUALS, 2),
        taboos=_pickn(rng, ["waste", "cowardice", "oathbreaking", "impiety",
                            "foreign coin", "grave theft"], 2),
        symbols=_pickn(rng, SYMBOLS, 2),
        architecture=_pick(rng, STYLES),
        cities={city.id: 0.55},
    )
    culture.history.append(f"Coalesced in {city.name}.")
    society.cultures[cid] = culture
    return [{"tick": world.tick, "type": "culture", "culture_id": cid,
             "city_id": city.id, "title": f"{name} took shape",
             "detail": f"{city.name}'s customs became a recognizable culture."}]


def _spread(society, world) -> None:
    rng = world.rng.stream("culture")
    for culture in society.cultures.values():
        if not culture.alive:
            continue
        for cid in list(culture.cities):
            culture.cities[cid] = min(1.0, culture.cities[cid] + 0.004)
            city = world.cities.get(cid)
            if city:
                _apply_culture(city, culture, culture.cities[cid])
        if rng.random() < 0.35 and culture.cities:
            src_id = max(culture.cities, key=culture.cities.get)
            src = world.cities.get(src_id)
            if src:
                near = sorted((c for c in world.cities.values()
                               if c.alive and c.id not in culture.cities),
                              key=lambda c: abs(c.pos[0]-src.pos[0]) + abs(c.pos[1]-src.pos[1]))
                if near:
                    target = near[0]
                    distance = abs(target.pos[0]-src.pos[0]) + abs(target.pos[1]-src.pos[1])
                    if distance < 48 or src.wealth > 20:
                        culture.cities[target.id] = max(culture.cities.get(target.id, 0), 0.12)
        culture.cities = {cid: share for cid, share in culture.cities.items()
                          if cid in world.cities and world.cities[cid].alive}
        culture.alive = bool(culture.cities)


def _apply_culture(city, culture: Culture, share: float) -> None:
    strength = min(1.0, share)
    vals = set(culture.values)
    if "trade" in vals:
        city.wealth += 0.015 * strength
        city.stocks["luxury"] = city.stocks.get("luxury", 0.0) + 0.01 * strength
    if "learning" in vals:
        city.culture += 0.012 * strength
        city.stocks["knowledge"] = city.stocks.get("knowledge", 0.0) + 0.04 * strength
    if "piety" in vals:
        city.culture += 0.01 * strength
        city.buildings["temples"] = max(city.buildings.get("temples", 0), int(city.culture / 60))
    if "discipline" in vals:
        city.unrest = max(0.0, city.unrest - 0.002 * strength)
    if "freedom" in vals and city.unrest > 0.25:
        city.culture += 0.008 * strength
    if "craft" in vals:
        city.stocks["wood"] = city.stocks.get("wood", 0.0) + 0.02 * strength
        city.stocks["metal"] = city.stocks.get("metal", 0.0) + 0.01 * strength


def _pick(rng, seq):
    return seq[int(rng.integers(0, len(seq)))]


def _pickn(rng, seq, n):
    return list({ _pick(rng, seq) for _ in range(n) })
