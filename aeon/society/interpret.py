"""The interpretation layer — the LLM as historian, biographer, and journalist.

The simulation is the source of truth; this module turns its facts into *meaning*. It
never invents facts: it is handed a fact-sheet assembled from real simulation state and
asked only to phrase it. Results are cached (keyed by a content signature) so unchanged
content is never regenerated, and generation is awaited on demand (off the sim hot
path) — async, incremental, mobile-safe.

  build_biography_facts(...)  → grounded fact-sheet for a person
  build_newspaper_facts(...)  → grounded fact-sheet of recent world events
  Cache                       → signature-keyed text store, persisted with the world
"""

from __future__ import annotations

import json
from pathlib import Path


class Cache:
    """A tiny text cache keyed by (kind, id) → {sig, text}. Regenerates only when the
    content signature changes (e.g. a person's life moved on)."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def get(self, kind: str, key, sig: str) -> str | None:
        e = self.store.get(f"{kind}:{key}")
        return e["text"] if e and e["sig"] == sig else None

    def put(self, kind: str, key, sig: str, text: str) -> None:
        self.store[f"{kind}:{key}"] = {"sig": sig, "text": text}

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.store))

    def load(self, path) -> None:
        p = Path(path)
        if p.exists():
            try:
                self.store = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                self.store = {}


BIO_SYSTEM = (
    "You are a biographer in a living world. Using ONLY the facts provided, write a "
    "2–4 sentence biography of this person that explains why they mattered. Be factual "
    "and grounded — invent nothing beyond the facts. Period voice; never mention that "
    "this is a game or simulation.")

NEWS_SYSTEM = (
    "You are the editor of a world chronicle newspaper. Using ONLY the events provided, "
    "write 3–5 very short news items (a bold headline line, then one sentence). Group by "
    "theme where natural (war, trade, faith, migration). Factual and grounded — invent "
    "nothing. Period voice; never mention that this is a game or simulation.")


def build_biography_facts(person, world, society, life_chronicle, family) -> str:
    """Assemble a person's real life into a fact-sheet for the biographer."""
    civ = world.civilizations.get(person.civ_id)
    rel = society.religions.get(person.religion_id) if person.religion_id else None
    founded = [r.name for r in society.religions.values() if r.founder_id == person.id]
    founded += [f.name for f in society.factions.values() if f.founder_id == person.id]
    events = "; ".join(e["text"] for e in life_chronicle[:10]) or "little recorded"
    kin = []
    if family.get("spouse"):
        kin.append(f"married to {family['spouse']['name']}")
    if family.get("children"):
        kin.append(f"{len(family['children'])} children")
    status = ("a person of great standing" if person.status > 0.7
              else "of middling rank" if person.status > 0.3 else "of humble station")
    return (
        f"NAME: {person.name} (House {person.name.split(' ')[-1]})\n"
        f"LIFE: {person.summary()}; {status}.\n"
        f"PEOPLE: {civ.name if civ else 'free folk'}"
        f"{', faithful to ' + rel.name if rel else ''}.\n"
        f"FAMILY: {', '.join(kin) or 'no recorded family'}.\n"
        f"DEEDS: {('founded ' + ', '.join(founded) + '. ') if founded else ''}"
        f"Wealth {'great' if person.wealth > 15 else 'modest' if person.wealth > 3 else 'scant'}.\n"
        f"LIFE EVENTS: {events}.\n"
        f"{'DIED of ' + (person.death_cause or 'age') + f', aged {person.age}.' if not person.alive else f'Still living, aged {person.age}.'}"
    )


def person_signature(person) -> str:
    """Changes when the person's life materially advances → triggers regeneration."""
    return f"{len(person.milestones)}:{len(person.memory.items)}:{int(person.alive)}:{person.age}"


CITY_SYSTEM = (
    "You are a city historian in a living world. Using ONLY the facts provided, write "
    "a 3–5 sentence history of this city: why it rose, what shaped it, and its state "
    "today. Be factual and grounded — invent nothing. Period voice; never mention that "
    "this is a game or simulation.")

RELIGION_SYSTEM = (
    "You are a historian of religions. Using ONLY the facts provided, write a 3–4 "
    "sentence account of this faith: its founding, what it teaches, how it spread, and "
    "any schism. Factual and grounded — invent no doctrine beyond the tenets given. "
    "Period voice; never mention that this is a game or simulation.")

CULTURE_SYSTEM = (
    "You are a cultural historian in a living world. Using ONLY the facts provided, "
    "write a 3–4 sentence account of this culture: where it began, what it values, "
    "how it is expressed, and where it has spread. Factual and grounded — invent "
    "nothing. Period voice; never mention that this is a game or simulation.")

DISCOVERY_SYSTEM = (
    "You are an archivist of discoveries in a living world. Using ONLY the facts "
    "provided, write a 2–3 sentence note explaining why this discovery matters. "
    "Factual and grounded — invent nothing. Period voice; never mention that this is "
    "a game or simulation.")


def build_city_facts(city, world, civ, religion, rel_share, chronicle_lines) -> str:
    state = []
    if city.famine > 0: state.append("famine")
    if city.plague > 0: state.append("plague")
    if city.unrest > 0.5: state.append("unrest")
    return (
        f"CITY: {city.name}, a {city.tier} ({city.specialty}).\n"
        f"PEOPLE: {civ.name if civ else 'free folk'}.\n"
        f"FOUNDED: {world.tick - city.founded_tick} ticks ago.\n"
        f"POPULATION: {int(city.population)}. WEALTH: "
        f"{'rich' if getattr(city,'wealth',0) > 40 else 'modest' if getattr(city,'wealth',0) > 8 else 'poor'}.\n"
        f"FAITH: {religion.name + f' ({int(rel_share*100)}% of the city)' if religion else 'local spirits'}.\n"
        f"CONDITION: {', '.join(state) or 'at peace'}.\n"
        f"RECORDED HISTORY:\n" + "\n".join(f"- {l}" for l in chronicle_lines)
    )


def city_signature(city, n_events: int) -> str:
    return f"{int(city.population/2000)}:{n_events}:{int(city.famine>0)}:{int(city.unrest>0.5)}"


def build_religion_facts(rel, world, population, followers) -> str:
    founder = population.get(rel.founder_id)
    parent = rel.schism_parent
    return (
        f"FAITH: {rel.name}.\n"
        f"FOUNDER: {rel.founder_name}"
        f"{' (still living)' if founder and founder.alive else ' (long dead)' if founder else ''}.\n"
        f"FOUNDED: {world.tick - rel.founded_tick} ticks ago at {rel.holy_city_name}.\n"
        f"TENETS (do not invent others): {'; '.join(rel.tenets)}.\n"
        f"SPREAD: held in {len(rel.cities)} cities; about {followers} faithful.\n"
        f"{'BORN OF A SCHISM from an older faith.' if parent else 'An original faith.'}"
    )


def religion_signature(rel, followers: int) -> str:
    return f"{len(rel.cities)}:{int(followers/5000)}:{int(rel.schism_parent is not None)}"


def build_newspaper_facts(world, events, season_name) -> str:
    lines = [f"THE WORLD: year {1 + world.tick // 1200}, {season_name}."]
    for e in events[:14]:
        lines.append(f"- [{e.get('type','event')}] {e.get('title','')}: {e.get('detail','')[:120]}")
    return "\n".join(lines)


def build_culture_facts(culture, world, city, chronicle_lines) -> str:
    spread = []
    for cid, share in sorted(culture.cities.items(), key=lambda kv: -kv[1])[:8]:
        c = world.cities.get(cid)
        if c:
            spread.append(f"{c.name} ({int(share * 100)}%)")
    return (
        f"CULTURE: {culture.name}.\n"
        f"ORIGIN: {culture.origin_city_name}, "
        f"{world.tick - culture.founded_tick} ticks ago.\n"
        f"ORIGIN CITY STATE: {city.name if city else culture.origin_city_name}; "
        f"population {int(city.population) if city else 'unknown'}, "
        f"wealth {round(city.wealth, 1) if city else 'unknown'}.\n"
        f"VALUES (do not invent others): {'; '.join(culture.values)}.\n"
        f"RITUALS: {'; '.join(culture.rituals)}.\n"
        f"TABOOS: {'; '.join(culture.taboos)}.\n"
        f"SYMBOLS: {'; '.join(culture.symbols)}.\n"
        f"ARCHITECTURE: {culture.architecture}.\n"
        f"SPREAD: {', '.join(spread) or 'only its origin is recorded'}.\n"
        f"RECORDED HISTORY:\n" + "\n".join(f"- {l}" for l in chronicle_lines)
    )


def culture_signature(culture, world) -> str:
    return f"{len(culture.cities)}:{int(sum(culture.cities.values()) * 100)}:{world.tick // 400}"


def build_discovery_facts(world, discovery) -> str:
    focus = discovery.get("focus", {})
    return (
        f"THE WORLD: year {1 + world.tick // 1200}.\n"
        f"DISCOVERY: {discovery.get('title', '')}.\n"
        f"SUBJECT: {discovery.get('subject', '')}.\n"
        f"DETAIL: {discovery.get('detail', '')}.\n"
        f"MEASURED VALUE: {discovery.get('value', '')}.\n"
        f"FOCUS: {focus.get('kind', '')}:{focus.get('id', '')}."
    )


def discovery_signature(discovery) -> str:
    focus = discovery.get("focus", {})
    return f"{discovery.get('key')}:{focus.get('kind')}:{focus.get('id')}:{discovery.get('value')}"
