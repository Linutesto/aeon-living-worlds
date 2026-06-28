"""CohortBatcher — compress a whole cohort of citizens into ONE teacher prompt.

The cardinal rule (CLAUDE.md): never call the LLM per-agent. So we gather a cohort —
the residents of a focal city, prioritizing those in crisis (famine/plague/unrest/war)
— compress each person to a single token-lean line, and ask the 27B to reason about
all of them at once, returning a structured decision per citizen. 50–500 citizens for
the price of one call.

Also builds the per-citizen `input` dict (world_state, citizen_profile, recent_events,
relationship_graph) that gets paired with the teacher's `output` to form a training
sample in the canonical format.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..agents.population import PopulationManager
from .encode import EMOTIONS, INTENTS
from ..agents.traits import ACTIONS


@dataclass
class Cohort:
    city_id: int | None
    city_name: str
    reason: str                       # why this cohort (crisis tag or "routine")
    persons: list = field(default_factory=list)


def world_state(world, society=None) -> dict:
    war = any(getattr(u, "kind", "") == "army" for u in world.units.values())
    famine = any(getattr(c, "famine", 0) > 0 for c in world.cities.values() if c.alive)
    plague = any(getattr(c, "plague", 0) > 0 for c in world.cities.values() if c.alive)
    unrest = max((getattr(c, "unrest", 0.0) for c in world.cities.values() if c.alive),
                 default=0.0)
    return {
        "year": int(getattr(world, "year", world.tick // 60)),
        "tick": world.tick,
        "war": war, "famine": famine, "plague": plague,
        "unrest": round(float(unrest), 3),
        "civ_count": sum(1 for c in world.civilizations.values()
                         if getattr(c, "alive", True)),
        "religion_count": len(getattr(society, "religions", {})) if society else 0,
        "faction_count": len(getattr(society, "factions", {})) if society else 0,
    }


def _crisis_score(world, city) -> float:
    s = float(getattr(city, "famine", 0) > 0) + float(getattr(city, "plague", 0) > 0)
    s += float(getattr(city, "unrest", 0.0))
    if any(getattr(u, "kind", "") == "army" and getattr(u, "dest_city", None) == city.id
           for u in world.units.values()):
        s += 1.0
    return s


class CohortBatcher:
    def __init__(self, *, min_size: int = 6, max_size: int = 300) -> None:
        self.min_size = min_size
        self.max_size = max_size

    def pick(self, world, population, society=None, rng=None) -> Cohort | None:
        """Choose a focal city's residents — crisis cities first.

        If nobody is observing a city (no focus), the teacher would have nothing to
        study, so it **self-sustains**: it focuses a rotating eligible city (crisis-first)
        on demand, so the mind keeps learning even when the player isn't looking."""
        focused = [world.cities.get(cid) for cid in population.focus_cities]
        focused = [c for c in focused
                   if c and c.alive and len(population.residents(c.id)) >= self.min_size]
        if not focused:
            # auto-focus the most worth-studying living city so a cohort always exists
            live = [c for c in world.cities.values() if c.alive and c.population > 0]
            if not live:
                return None
            live.sort(key=lambda c: (_crisis_score(world, c), c.population), reverse=True)
            target = live[0]
            population.focus(world, target.id)         # materializes its residents (LOD)
            focused = [target] if len(population.residents(target.id)) >= self.min_size \
                else []
            if not focused:
                return None
        focused.sort(key=lambda c: (_crisis_score(world, c), c.population), reverse=True)
        city = focused[0]
        residents = population.residents(city.id)
        if len(residents) < self.min_size:
            return None
        score = _crisis_score(world, city)
        reason = ("crisis" if score >= 1.0 else "routine")
        if len(residents) > self.max_size:
            chooser = rng or __import__("random")
            residents = chooser.sample(residents, self.max_size)
        return Cohort(city_id=city.id, city_name=city.name, reason=reason,
                      persons=residents)

    # ------------------------------------------------------ per-person inputs
    @staticmethod
    def recent_events(p, n: int = 6) -> list[dict]:
        return [{"kind": m.kind, "valence": round(m.valence, 2), "tick": m.tick,
                 "text": m.text} for m in p.memory.top(n)]

    @staticmethod
    def relationship_graph(p) -> dict:
        rels = list(p.relationships.values())
        strengths = [r.strength for r in rels] or [0.0]
        return {
            "n": len(rels),
            "mean_strength": round(sum(strengths) / len(strengths), 3),
            "n_kin": len(p.kin()),
            "has_partner": p.partner_id is not None,
            "n_rivals": sum(1 for r in rels if r.kind in ("rival", "enemy")),
            "n_friends": sum(1 for r in rels if r.kind in ("friend", "partner")),
        }

    @staticmethod
    def citizen_profile(p) -> dict:
        return {
            "id": p.id, "name": p.name, "age": p.age, "sex": p.sex,
            "profession": p.profession, "social_class": p.social_class,
            "health": round(p.health, 2), "wealth": round(p.wealth, 2),
            "status": round(p.status, 2), "mood": round(p.mood, 2),
            "stress": round(p.stress, 2), "grievance": round(p.grievance, 2),
            "goal": p.dominant_goal(), "religion_id": p.religion_id,
            "personality": {k: round(v, 2) for k, v in p.personality.items()},
        }

    @staticmethod
    def features(p, city, world) -> list[float]:
        # coerce to native floats — some city stats are numpy scalars (not JSON-safe)
        return [float(x) for x in PopulationManager.features(p, city, world)]

    # ------------------------------------------------------------ the prompt
    def _line(self, p) -> str:
        rel = self.relationship_graph(p)
        top = p.memory.top(1)
        recent = top[0].text[:60] if top else "—"
        return (f"[{p.id}] {p.name}, {p.age}{p.sex} {p.profession}/{p.social_class} | "
                f"H{p.health:.1f} W{p.wealth:.0f} St{p.status:.1f} mood{p.mood:+.1f} "
                f"stress{p.stress:.1f} griev{p.grievance:.1f} | goal:{p.dominant_goal()} "
                f"kin{rel['n_kin']} {'partnered' if p.partner_id else 'single'} | "
                f'recent:"{recent}"')

    def build_prompt(self, world, cohort: Cohort, society=None) -> tuple[str, str]:
        ws = world_state(world, society)
        system = (
            "You are the collective subconscious of a living civilization: you decide what "
            "its people do, feel, remember, and intend, grounded ONLY in the facts given. "
            "Reason about the whole cohort at once. Return STRICT JSON:\n"
            '{"citizens":[{"id":<int>,"action":<one of '
            f"{ACTIONS}>,\"emotion\":<one of {EMOTIONS}>,"
            '"future_intent":<one of ' f"{INTENTS}>,"
            '"target_kind":"home|workplace|city|citizen|market|temple|resource|shelter",'
            '"target_reason":"<why this destination fits>",'
            '"memory":"<one vivid first-person sentence they would now hold>",'
            '"dialogue":"<one short line they might say aloud>"}]}\n'
            "Every id from the cohort must appear exactly once. No prose outside the JSON."
        )
        header = (
            f"WORLD: year {ws['year']}, civ_count {ws['civ_count']}, "
            f"religions {ws['religion_count']}, factions {ws['faction_count']}. "
            f"Pressures: {'WAR ' if ws['war'] else ''}{'FAMINE ' if ws['famine'] else ''}"
            f"{'PLAGUE ' if ws['plague'] else ''}unrest {ws['unrest']}.\n"
            f"CITY: {cohort.city_name} ({cohort.reason}). "
            f"COHORT of {len(cohort.persons)} citizens:\n")
        body = "\n".join(self._line(p) for p in cohort.persons)
        return system, header + body
