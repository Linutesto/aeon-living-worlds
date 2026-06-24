"""Emergent society — the macro structures that arise from individual minds.

This is the heart of the protocol's primary goal: every large-scale historical force
here is built from individual decisions, and it bends back on those individuals.

  beliefs.py    ideology axes + grievance: how circumstance shapes conviction.
  religion.py   faiths that are FOUNDED by charismatic individuals, spread through
                populations, schism, and can drive holy war.
  faction.py    guilds, orders, leagues, cults, and revolutionary movements that
                individuals found and join, accumulate influence, and through which
                micro incentives become macro politics (revolutions, coups).
  chronicle.py  the world's history book: an event-driven LLM writes legends and
                histories when major events fire (never per-tick).

`Society` ties them together; `Society.step(world, population)` runs once per
life-tick and returns timeline events; major ones are queued to the chronicler.
"""

from __future__ import annotations

from . import religion as _religion
from . import faction as _faction
from . import beliefs as _beliefs
from . import culture as _culture
from .chronicle import Chronicle

# events worthy of a hand-written chronicle passage
MAJOR = {"religion_founded", "schism", "faction_founded", "revolution",
         "holy_war", "coup", "civ_collapse"}


class Society:
    def __init__(self) -> None:
        self.religions: dict[int, _religion.Religion] = {}
        self.factions: dict[int, _faction.Faction] = {}
        self.cultures: dict[int, _culture.Culture] = {}
        self.chronicle = Chronicle()
        self._next_id = 1
        self.pending_chronicle: list[dict] = []   # drained by engine's chronicler

    def nid(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def _beliefs_pass(self, world, population) -> None:
        """Ensure every materialized soul has an ideology, and let circumstance move
        the grievances of those we are currently watching."""
        for p in population.people.values():
            if p.alive and not p.ideology:
                p.ideology = _beliefs.derive_ideology(p)
        for cid in population.focus_cities:
            city = world.cities.get(cid)
            for p in population.residents(cid):
                _beliefs.update_grievance(p, city, world)

    def step(self, world, population) -> list[dict]:
        events: list[dict] = []
        self._beliefs_pass(world, population)
        events += _culture.step(self, world, population)
        events += _religion.step(self, world, population)
        events += _faction.step(self, world, population)
        for e in events:
            if e.get("type") in MAJOR or e.get("major"):
                self.pending_chronicle.append(e)
        # keep the narration backlog bounded
        self.pending_chronicle = self.pending_chronicle[-40:]
        return events

    # ---- read helpers for serialization ----
    def religion_of_city(self, city_id: int):
        best, share = None, 0.0
        for r in self.religions.values():
            s = r.cities.get(city_id, 0.0)
            if s > share:
                best, share = r, s
        return best, share

    def culture_of_city(self, city_id: int):
        best, share = None, 0.0
        for c in self.cultures.values():
            s = c.cities.get(city_id, 0.0)
            if s > share:
                best, share = c, s
        return best, share
