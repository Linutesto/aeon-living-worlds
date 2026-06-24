"""Daily schedules — hour-by-hour citizen routines (Phase 1).

A citizen's day is a pure function of the world tick and their own state: profession
sets the rhythm, and age, health, faith, family, the season, and war/famine bend it.
Because it is derived (not stored), it needs no save/load and stays in lockstep with
the deterministic clock. The output drives Follow Mode, the live HUD, and the "why"
explanation that grounds a citizen's behaviour in real simulation state.

A day is `TICKS_PER_DAY` ticks; one tick is one hour.
"""

from __future__ import annotations

from ..sim import season as _season

TICKS_PER_DAY = 24


def hour(tick: int) -> int:
    return int(tick) % TICKS_PER_DAY


def time_of_day(h: int) -> str:
    if h < 5:  return "night"
    if h < 8:  return "dawn"
    if h < 12: return "morning"
    if h < 14: return "midday"
    if h < 18: return "afternoon"
    if h < 21: return "evening"
    return "night"


# per-profession hourly blocks: list of (start_hour, activity-key). The last block
# wraps to midnight. Activity keys map to phrases + destinations below.
_DEFAULT = [(0, "sleep"), (6, "eat"), (7, "work"), (12, "market"),
            (13, "work"), (18, "family"), (20, "socialize"), (22, "sleep")]

_ROUTINES = {
    "farmer":  [(0, "sleep"), (5, "field"), (12, "market"), (13, "field"),
                (18, "family"), (21, "sleep")],
    "priest":  [(0, "sleep"), (5, "pray"), (7, "service"), (11, "pastoral"),
                (14, "study"), (17, "sermon"), (20, "pray"), (22, "sleep")],
    "merchant":[(0, "sleep"), (6, "eat"), (7, "market"), (11, "travel"),
                (15, "warehouse"), (18, "negotiate"), (20, "family"), (22, "sleep")],
    "trader":  [(0, "sleep"), (6, "market"), (10, "travel"), (16, "market"),
                (19, "family"), (22, "sleep")],
    "soldier": [(0, "guard"), (6, "train"), (10, "patrol"), (14, "train"),
                (18, "rest"), (20, "guard")],
    "scholar": [(0, "sleep"), (6, "study"), (9, "academy"), (13, "teach"),
                (16, "write"), (19, "socialize"), (22, "sleep")],
    "scribe":  [(0, "sleep"), (6, "study"), (8, "write"), (13, "academy"),
                (17, "study"), (20, "family"), (22, "sleep")],
    "noble":   [(0, "sleep"), (8, "court"), (11, "administer"), (14, "diplomacy"),
                (17, "leisure"), (20, "feast"), (23, "sleep")],
    "laborer": [(0, "sleep"), (6, "eat"), (7, "workshop"), (12, "market"),
                (13, "construct"), (18, "rest"), (21, "sleep")],
    "smith":   [(0, "sleep"), (6, "eat"), (7, "workshop"), (13, "market"),
                (14, "workshop"), (19, "family"), (22, "sleep")],
    "miner":   [(0, "sleep"), (5, "mine"), (13, "market"), (14, "mine"),
                (18, "rest"), (21, "sleep")],
    "healer":  [(0, "sleep"), (6, "tend"), (12, "gather"), (13, "tend"),
                (19, "family"), (22, "sleep")],
    "fisher":  [(0, "sleep"), (4, "fish"), (12, "market"), (14, "fish"),
                (18, "family"), (21, "sleep")],
    "sailor":  [(0, "sleep"), (5, "dock"), (8, "voyage"), (17, "dock"),
                (19, "tavern"), (23, "sleep")],
    "builder": [(0, "sleep"), (6, "eat"), (7, "construct"), (12, "market"),
                (13, "construct"), (18, "rest"), (21, "sleep")],
    "hunter":  [(0, "sleep"), (5, "hunt"), (13, "market"), (15, "hunt"),
                (19, "family"), (22, "sleep")],
    "child":   [(0, "sleep"), (7, "play"), (12, "eat"), (13, "learn"),
                (17, "play"), (20, "sleep")],
}

# activity key -> (verb phrase, destination kind: "home" | "work" | "" )
_PHRASE = {
    "sleep": ("asleep", "home"), "eat": ("taking a meal", "home"),
    "rest": ("resting", "home"), "family": ("at home with family", "home"),
    "socialize": ("among friends", ""), "market": ("at the market", ""),
    "work": ("at work", "work"), "field": ("working the fields", "work"),
    "pray": ("at prayer", "work"), "service": ("leading temple service", "work"),
    "pastoral": ("tending to the faithful", ""), "sermon": ("preaching a sermon", "work"),
    "study": ("at their studies", "work"), "academy": ("at the academy", "work"),
    "teach": ("teaching pupils", "work"), "write": ("writing", "work"),
    "travel": ("travelling the roads", ""), "warehouse": ("counting stock", "work"),
    "negotiate": ("striking a bargain", ""), "guard": ("on guard duty", "work"),
    "train": ("at drill", "work"), "patrol": ("on patrol", ""),
    "court": ("at court", "work"), "administer": ("at administration", "work"),
    "diplomacy": ("receiving envoys", "work"), "leisure": ("at leisure", ""),
    "feast": ("at a feast", ""), "workshop": ("at the workshop", "work"),
    "construct": ("on the building site", ""), "mine": ("down the mine", "work"),
    "tend": ("tending the sick", "work"), "gather": ("gathering herbs", ""),
    "fish": ("out fishing", ""), "dock": ("working the docks", "work"),
    "voyage": ("at sea", ""), "tavern": ("at the tavern", ""),
    "hunt": ("hunting in the wilds", ""), "play": ("at play", "home"),
    "learn": ("at lessons", ""), "mourn": ("in mourning", "home"),
    "forage": ("foraging for food", ""), "hearth": ("mending tools by the hearth", "home"),
    "deployed": ("marched to the war front", ""),
}


def _blocks(profession: str):
    return _ROUTINES.get(profession, _DEFAULT)


def _activity_key(person, world, h: int) -> str:
    blocks = _blocks(person.profession if person.age >= 14 else "child")
    key = blocks[-1][1]
    for start, act in blocks:
        if h >= start:
            key = act
        else:
            break
    return key


def _override(person, world, key: str) -> str:
    """Bend the routine to real circumstance — health, faith, famine, war, season."""
    city = world.cities.get(person.home_city) if person.home_city else None
    awake = key != "sleep"
    if person.health < 0.4 and awake:
        return "rest"
    if awake and city and city.famine > 0 and key in ("work", "field", "market", "socialize"):
        return "forage"
    if awake and person.profession == "soldier" and city:
        civ = world.civilizations.get(city.civ_id)
        if civ and getattr(civ, "war_intents", None):
            return "deployed"
    if awake and person.profession == "farmer" and _season.index(world.tick) == 3 \
            and key == "field":
        return "hearth"                      # winter: no field work
    # the devout add a dawn prayer
    if key in ("work", "field", "workshop") and person.ideology.get("piety", 0) > 0.7 \
            and 5 <= hour(world.tick) <= 6:
        return "pray"
    return key


def schedule(person, world) -> dict:
    """The citizen's current and next activity, destination, and a grounded reason."""
    t = world.tick
    h = hour(t)
    key = _override(person, world, _activity_key(person, world, h))
    phrase, dest_kind = _PHRASE.get(key, ("going about their day", ""))
    # peek the next distinct activity within the next 8 hours
    nxt_key, nxt_h = key, h
    for dh in range(1, 9):
        k = _override(person, world, _activity_key(person, world, (h + dh) % TICKS_PER_DAY))
        if k != key:
            nxt_key, nxt_h = k, (h + dh) % TICKS_PER_DAY
            break
    dest = (person.work_building if dest_kind == "work"
            else person.home_building if dest_kind == "home" else None)
    return {
        "hour": h, "time_of_day": time_of_day(h), "activity": key,
        "phrase": phrase, "destination": dest, "destination_kind": dest_kind,
        "next_activity": nxt_key, "next_hour": nxt_h,
        "next_phrase": _PHRASE.get(nxt_key, ("…", ""))[0],
        "why": _why(person, world, key),
    }


def _why(person, world, key: str) -> str:
    """A one-line explanation grounded in this person's real state."""
    city = world.cities.get(person.home_city) if person.home_city else None
    bits = []
    if key == "forage" and city and city.famine > 0:
        bits.append(f"famine grips {city.name}, so they forage to survive")
    elif key == "deployed":
        bits.append("their people are at war and every soldier is called")
    elif key == "pray" and person.ideology.get("piety", 0) > 0.7:
        bits.append("deeply devout, they keep the dawn prayer")
    elif key == "rest" and person.health < 0.4:
        bits.append("too unwell to work")
    elif key == "hearth":
        bits.append("winter has frozen the fields")
    else:
        bits.append(f"the {time_of_day(hour(world.tick))} routine of a {person.profession}")
    if person.partner_id is None and person.children:
        bits.append("a widowed parent")
    return "; ".join(bits)
