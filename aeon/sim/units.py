"""Units — people moving through the world, in real time.

This is what makes the world *observable*: you don't read that a war happened, you
watch an army march from one city to another and besiege it. Kinds:

  civilian  — ambient life wandering near a city's edge (shows where people live)
  trader    — short hops between nearby friendly cities, carrying wealth
  caravan   — long-haul trade between distant cities
  migrant   — flees a famine/unrest city toward a prosperous one (migration flows)
  explorer  — strikes out from a frontier city into the unknown
  army       — raised on a war intent; marches to an enemy city and besieges it

Movement is plain steering toward a target at a per-kind speed. Arrivals resolve
into economic/territorial effects (trade income, migration, conquest). A global cap
keeps the population of *units* bounded and the renderer fast; the client interpolates
between snapshots so motion looks smooth at 60fps regardless of sim/broadcast rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import world as _w
from . import cities as _cities
from . import season as _season

# kind -> (speed tiles/tick, base ttl ticks)
KINDS = {
    "civilian": (0.25, 90),
    "trader":   (0.9, 400),
    "caravan":  (0.6, 900),
    "migrant":  (0.5, 700),
    "explorer": (1.1, 600),
    "army":     (0.55, 1200),
}
KIND_CODE = {k: i for i, k in enumerate(KINDS)}

MAX_UNITS = 340
MAX_CIVILIANS = 150


@dataclass
class Unit:
    id: int
    kind: str
    civ_id: int
    pos: list                       # [y, x] floats (mutable for cheap stepping)
    target: tuple                   # (y, x)
    speed: float
    born_tick: int
    ttl: int
    origin_city: int | None = None
    dest_city: int | None = None
    payload: float = 0.0            # army strength / trade value / migrant count
    cargo: dict[str, float] = field(default_factory=dict)
    state: str = "moving"

    @property
    def code(self) -> int:
        return KIND_CODE[self.kind]


def _spawn(world, kind, civ_id, pos, target, **kw) -> Unit:
    speed, ttl = KINDS[kind]
    u = Unit(id=world.new_unit_id(), kind=kind, civ_id=civ_id,
             pos=[float(pos[0]), float(pos[1])], target=(float(target[0]), float(target[1])),
             speed=speed * (0.8 + 0.4 * world.rng.stream("unit").random()),
             born_tick=world.tick, ttl=ttl, **kw)
    world.units[u.id] = u
    return u


def _alive_cities(world, civ_id=None):
    return [c for c in world.cities.values()
            if c.alive and (civ_id is None or c.civ_id == civ_id)]


def _nearest(city, pool, max_dist=1e9, exclude_self=True):
    best, bd = None, max_dist
    for o in pool:
        if exclude_self and o.id == city.id:
            continue
        d = abs(o.pos[0] - city.pos[0]) + abs(o.pos[1] - city.pos[1])
        if d < bd:
            best, bd = o, d
    return best


def step(world: "_w.WorldState") -> list[dict]:
    out: list[dict] = []
    _spawn_phase(world)
    out += _resolve_wars(world)            # turn civ war-intents into armies
    out += _move_and_resolve(world)
    # prune dead/expired
    for uid in [u.id for u in world.units.values()
                if u.state == "done" or world.tick - u.born_tick > u.ttl]:
        del world.units[uid]
    return out


# ---------------- spawning ----------------

def _spawn_phase(world) -> None:
    cities = _alive_cities(world)
    if not cities:
        return
    n_civ = sum(1 for u in world.units.values() if u.kind == "civilian")

    for city in cities:
        cy, cx = city.pos
        # ambient civilians around populated places (visual life)
        if (n_civ < MAX_CIVILIANS and len(world.units) < MAX_UNITS
                and world.rng.chance("civ", min(0.6, city.population / 8000))):
            ang = world.rng.stream("civ").random() * 2 * math.pi
            r = city.influence_radius * (0.4 + 0.6 * world.rng.stream("civ").random())
            tgt = (cy + r * math.sin(ang), cx + r * math.cos(ang))
            _spawn(world, "civilian", city.civ_id, (cy, cx), tgt)
            n_civ += 1

        if len(world.units) >= MAX_UNITS:
            continue

        # traders move scarce goods toward higher prices
        if city.wealth > 5 and world.rng.chance("trade", 0.04):
            friends = _alive_cities(world, city.civ_id)
            dest, good = _best_trade(world, city, friends)
            if dest and good:
                kind = "caravan" if (abs(dest.pos[0]-cy)+abs(dest.pos[1]-cx)) > 24 else "trader"
                qty = min(city.stocks.get(good, 0.0) * 0.12, 20.0, max(1.0, city.wealth))
                city.stocks[good] = max(0.0, city.stocks.get(good, 0.0) - qty)
                u = _spawn(world, kind, city.civ_id, (cy, cx), dest.pos,
                           origin_city=city.id, dest_city=dest.id,
                           payload=min(city.wealth, 15), cargo={good: qty})
                city.wealth -= u.payload

        # migrants flee famine / unrest toward prosperity — but winter roads slow them
        mig_chance = 0.25 * _season.travel_factor(world.tick)
        if (city.famine > 0 or city.unrest > 0.5) and world.rng.chance("mig", mig_chance):
            targets = [c for c in cities if c.famine == 0 and c.id != city.id]
            if targets:
                dest = max(targets, key=lambda c: c.food_production - c.population * 1e-4)
                leaving = city.population * 0.03
                city.population -= leaving
                _spawn(world, "migrant", city.civ_id, (cy, cx), dest.pos,
                       origin_city=city.id, dest_city=dest.id, payload=leaving)
                world.add_marker("migration", cy, cx, ttl=70, label=f"{city.name} exodus")

        # explorers from prosperous frontier cities
        if city.population > 2000 and world.rng.chance("explore", 0.01):
            ty = int(np.clip(cy + world.rng.stream("exp").integers(-50, 51), 1, world.height-2))
            tx = int(np.clip(cx + world.rng.stream("exp").integers(-50, 51), 1, world.width-2))
            _spawn(world, "explorer", city.civ_id, (cy, cx), (ty, tx), origin_city=city.id)


# ---------------- war → armies ----------------

def _resolve_wars(world) -> list[dict]:
    out: list[dict] = []
    for civ in world.civilizations.values():
        if not civ.alive:
            continue
        intents, civ.war_intents = civ.war_intents, []
        for intent in intents:
            src = world.cities.get(intent["from_city"])
            dst = world.cities.get(intent["to_city"])
            if not (src and dst and src.alive and dst.alive):
                continue
            strength = src.population * 0.08 * (0.5 + 0.1 * src.infrastructure)
            _spawn(world, "army", civ.id, src.pos, dst.pos,
                   origin_city=src.id, dest_city=dst.id, payload=strength)
            src.population *= 0.95          # the muster costs the city
            out.append({"tick": world.tick, "type": "war", "civ_id": civ.id,
                        "title": f"{civ.name} marches on {dst.name}",
                        "detail": f"An army of ~{int(strength)} sets out from {src.name}."})
            world.add_marker("march", src.pos[0], src.pos[1], ttl=50, label="army")
    return out


# ---------------- movement + arrival resolution ----------------

def _move_and_resolve(world) -> list[dict]:
    out: list[dict] = []
    for u in list(world.units.values()):
        ty, tx = u.target
        dy, dx = ty - u.pos[0], tx - u.pos[1]
        dist = math.hypot(dy, dx)
        if dist <= max(u.speed, 0.6):
            ev = _arrive(world, u)
            if ev:
                out.append(ev)
            if u.kind == "civilian":
                u.state = "done"     # civilians just wink out at the edge
            elif u.state != "captured":
                u.state = "done"
        else:
            u.pos[0] += dy / dist * u.speed
            u.pos[1] += dx / dist * u.speed
    return out


def _arrive(world, u: "Unit"):
    dst = world.cities.get(u.dest_city) if u.dest_city else None
    if u.kind in ("trader", "caravan") and dst and dst.alive:
        origin = world.cities.get(u.origin_city) if u.origin_city else None
        value = u.payload
        for good, qty in (u.cargo or {}).items():
            dst.stocks[good] = dst.stocks.get(good, 0.0) + qty
            value += qty * dst.prices.get(good, 1.0) * 0.15
        dst.wealth += value
        dst.culture += 0.15
        if origin:
            origin.wealth += value * 0.25
        return None
    if u.kind == "migrant" and dst and dst.alive:
        dst.population += u.payload
        return None
    if u.kind == "army":
        return _battle(world, u, dst)
    return None


def _battle(world, u: "Unit", dst):
    if not (dst and dst.alive):
        return None
    cy, cx = dst.pos
    defense = dst.population * 0.1 * (0.6 + 0.12 * dst.infrastructure)
    world.add_marker("battle", cy, cx, ttl=90, label=f"Siege of {dst.name}")
    attacker = world.civilizations.get(u.civ_id)
    defender = world.civilizations.get(dst.civ_id)
    if u.payload > defense:
        # conquest: the city changes hands
        dst.population *= 0.7
        if defender and dst.id in defender.city_ids:
            defender.city_ids.remove(dst.id)
        dst.civ_id = u.civ_id
        if attacker:
            attacker.city_ids.append(dst.id)
        dst.unrest = min(1.0, dst.unrest + 0.4)
        dst.history.append(f"Conquered by {attacker.name if attacker else '?'} at tick {world.tick}.")
        return {"tick": world.tick, "type": "war", "civ_id": u.civ_id,
                "title": f"{attacker.name if attacker else 'An army'} captured {dst.name}",
                "detail": f"{dst.name} fell after a siege; ~{int(dst.population*0.43)} lost.",
                "why": {"army_strength": round(u.payload, 1),
                        "defense": round(defense, 1),
                        "infrastructure": round(dst.infrastructure, 2),
                        "unrest": round(dst.unrest, 2)}}
    # repelled
    dst.population *= 0.95
    return {"tick": world.tick, "type": "war", "civ_id": dst.civ_id,
            "title": f"{dst.name} repelled the siege",
            "detail": f"{dst.name}'s defenders held the walls.",
            "why": {"army_strength": round(u.payload, 1),
                    "defense": round(defense, 1),
                    "infrastructure": round(dst.infrastructure, 2)}}


def _best_trade(world, city, pool):
    best = (None, None, 0.0)
    for dest in pool:
        if dest.id == city.id:
            continue
        d = abs(dest.pos[0] - city.pos[0]) + abs(dest.pos[1] - city.pos[1])
        if d > 55:
            continue
        for good in ("food", "wood", "stone", "metal", "energy", "luxury", "knowledge"):
            if city.stocks.get(good, 0.0) < 2:
                continue
            margin = dest.prices.get(good, 1.0) - city.prices.get(good, 1.0)
            score = margin - d * 0.01
            if score > best[2]:
                best = (dest, good, score)
    if best[2] <= 0.05:
        return _nearest(city, pool, max_dist=45), "luxury" if city.stocks.get("luxury", 0) > 2 else "food"
    return best[0], best[1]
