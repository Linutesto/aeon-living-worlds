"""The LOD persona pool — how "every person is real" runs on one machine.

A bounded pool of fully-realized individuals is concentrated where the observer is
looking. When a city is *focused* (selected/zoomed on the dashboard) its residents
are **materialized**: born with plausible ages, professions matching the city's
specialty, families, friends and rivals, and seeded memories drawn from the city's
real history. They then live — aging, working, courting, feuding, migrating, dying,
remembering — emitting the small human stories that make the world worth watching.
When attention leaves and the pool is over budget, the least-relevant individuals in
unfocused cities are released (their city keeps its statistical population, and new
individuals are re-materialized if you return).

This module is Level-1/Level-2 of the hierarchy: cheap per-agent utility cognition
(traits.py) plus family/relationship structure. The optional per-species neural
policy (ai/species_policy.py) can bias decisions once trained.
"""

from __future__ import annotations

import logging

import numpy as np

from . import traits
from .person import Person, Relationship

log = logging.getLogger("aeon.population")

MAX_PEOPLE = 4000                  # global persona-pool budget
LIFE_INTERVAL = 12                 # sim ticks between life updates
TARGET_BY_TIER = {"hamlet": 8, "village": 16, "town": 30,
                  "city": 50, "metropolis": 80}

# Individuating colour pools — drawn on at materialization so two same-profession,
# same-class neighbours still read as different people.
_QUIRKS = [
    "hums while working", "never makes eye contact", "collects odd stones",
    "quotes dead poets", "distrusts anyone who smiles too much", "always early",
    "talks to animals", "counts everything twice", "keeps a hidden journal",
    "laughs at the wrong moments", "refuses to sit with their back to a door",
    "gives away food they can't spare", "argues with the weather",
]
_SPEECH_STYLES = ["terse", "florid", "blunt", "sardonic", "warm", "formal",
                  "rambling", "stammering", "gruff", "honeyed", "preachy", "plain"]
_PROBLEMS = [
    "drowning in debt", "estranged from a sibling", "haunted by a past failure",
    "secretly in love with the wrong person", "losing their eyesight",
    "addicted to drink", "feuding with a powerful neighbour",
    "raising a child alone", "passed over for promotion one too many times",
    "keeping a dangerous secret", "homesick for a place that no longer exists",
    "caring for a dying parent",
]
_PAST_EVENTS = [
    "survived a famine as a child", "lost a sibling to plague",
    "witnessed a battle at the city gates", "once met a foreign dignitary",
    "saved a neighbour from a fire", "was robbed on the road and never forgot it",
    "inherited and squandered a small fortune", "fled a burning village",
    "apprenticed under a famous master", "stood trial and was acquitted",
]


class PopulationManager:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.people: dict[int, Person] = {}
        self._next_id = 1
        self.focus_cities: set[int] = set()      # currently observed
        self.notable: set[int] = set()           # always-kept (leaders, elders)
        self._last_life_tick = 0
        # learning signal buffer for the species policies (ai/species_policy.py)
        self.experience: list[dict] = []

    # ------------------------------------------------------------------ ids
    def _nid(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    # ---------------------------------------------------------- materialize
    def focus(self, world, city_id: int) -> int:
        """Mark a city focused and ensure its residents exist. Returns count."""
        self.focus_cities.add(city_id)
        city = world.cities.get(city_id)
        if not city or not city.alive:
            return 0
        target = TARGET_BY_TIER.get(city.tier, 12)
        have = len(self.residents(city_id))
        if have < target:
            self._materialize(world, city, target - have)
        self._enforce_budget()
        return len(self.residents(city_id))

    def residents(self, city_id: int, include_dead: bool = False) -> list[Person]:
        return [p for p in self.people.values()
                if (include_dead or p.alive) and p.home_city == city_id]

    def _species_of(self, world, civ_id: int):
        civ = world.civilizations.get(civ_id)
        if civ:
            # the citizens carry their nation's *people* name as their lineage/culture,
            # so each civ's individuals read as a distinct folk.
            people = getattr(civ, "people", None) or (
                world.species.get(civ.origin_species_id).name
                if world.species.get(civ.origin_species_id) else "Folk")
            return people, (civ.origin_species_id or 0), civ.name
        return "Folk", 0, "the wild"

    @staticmethod
    def _civ_identity(world, civ_id: int) -> dict:
        """The nation's character a new citizen inherits. Safe defaults if civless."""
        civ = world.civilizations.get(civ_id)
        if civ is None:
            return {"axes": {}, "traits": [], "desires": [], "ideology": "Tribal",
                    "economic": 0.5, "military": 0.5, "religious": 0.5,
                    "exploration": 0.5, "traditionalism": 0.5, "name": "the wild"}
        axes = getattr(civ, "ideology_axes", {}) or {}
        return {
            "axes": axes,
            "traits": getattr(civ, "cultural_traits", []) or [],
            "desires": getattr(civ, "preferred_desires", []) or [],
            "ideology": getattr(civ, "ideology", "Tribal"),
            "economic": getattr(civ, "economic_bias", 0.5),
            "military": getattr(civ, "military_bias", 0.5),
            "religious": getattr(civ, "religious_bias", 0.5),
            "exploration": getattr(civ, "exploration_bias", 0.5),
            "traditionalism": axes.get("traditionalism", 0.5),
            "name": civ.name,
        }

    def _materialize(self, world, city, n: int) -> None:
        rng = world.rng.stream("person")
        species, sid, civ_name = self._species_of(world, city.civ_id)
        ident = self._civ_identity(world, city.civ_id)
        # professions weighted by BOTH the city's geography (its specialty) and the
        # nation's character (a warhost breeds soldiers; a league breeds traders).
        prof_pool = self._profession_pool(city, ident)
        new_ids: list[int] = []
        for _ in range(n):
            pid = self._nid()
            sex = "f" if rng.random() < 0.5 else "m"
            age = int(rng.integers(1, 78))
            profession = (prof_pool[int(rng.integers(0, len(prof_pool)))]
                          if age >= 14 else "child")
            personality = traits.gen_personality(rng)
            cls = self._class_for(rng, city)
            home_building = self._building_id_for(city, self._home_kind_for(cls), pid)
            work_building = self._building_id_for(city, self._work_kind_for(profession), pid)
            ideology = self._ideology_for(ident, personality, rng)
            person = Person(
                id=pid, name=traits.gen_name(rng), sex=sex, age=age,
                species=species, species_id=sid, civ_id=city.civ_id,
                home_city=city.id, birthplace=city.name,
                profession=profession,
                education=self._education(cls, rng),
                social_class=cls,
                personality=personality,
                goals=traits.goal_weights(personality, rng),
                skills=traits.gen_skills(rng, profession),
                beliefs=self._beliefs(ident, city, personality, rng),
                fears=self._fears(city, rng),
                preferences=self._prefs(rng),
                ideology=ideology,
                wealth=round(self._wealth_for(cls) * (0.5 + rng.random()), 2),
                status={"destitute": .05, "commoner": .2, "freeholder": .35,
                        "merchant": .5, "gentry": .7, "noble": .9}[cls],
                rootedness=round(float(rng.beta(2, 2)), 2),
                # not everyone is hale and content: health/mood/grievance now vary with
                # personality, class and the state of their city.
                health=round(float(np.clip(rng.beta(5, 2)
                              - (0.15 if cls == "destitute" else 0.0)
                              - (0.1 if city.famine > 0 else 0.0), 0.2, 1.0)), 2),
                grievance=self._grievance_for(city, cls, ideology, personality, rng),
                mood=round(float(rng.normal(0, 0.2)), 2),
                stress=round(float(rng.beta(2, 5)), 2),
                possessions=self._possessions_for(cls, profession),
                ambitions=self._ambitions(personality, rng),
                home_building=home_building,
                work_building=work_building,
                born_tick=world.tick - age * 50,
                # individuating colour
                quirk=self._pick_one(rng, _QUIRKS),
                speech_style=self._pick_one(rng, _SPEECH_STYLES),
                life_goal=self._life_goal(ident, personality, rng),
                personal_problem=self._pick_one(rng, _PROBLEMS),
                past_event=self._pick_one(rng, _PAST_EVENTS),
                civ_loyalty=round(float(np.clip(
                    0.4 + ident["traditionalism"] * 0.3 + personality["agreeableness"] * 0.2
                    + rng.normal(0, 0.12), 0.0, 1.0)), 2),
                class_tension=round(float(np.clip(
                    (0.5 if cls in ("destitute", "commoner") else 0.1)
                    + ideology.get("radicalism", 0.0) * 0.4 + rng.normal(0, 0.1),
                    0.0, 1.0)), 2),
                local_identity=f"a child of {city.name}",
            )
            self._occupy_buildings(city, person)
            # a couple of seeded memories grounded in real history
            person.remember(f"I was born in {city.name}, a {city.specialty.lower()}.",
                            "birth", person.born_tick, 0.3)
            if city.history:
                h = city.history[int(rng.integers(0, len(city.history)))]
                person.remember(h, "civic", world.tick - int(rng.integers(0, 200)), -0.1)
            person.milestones.append(f"Born in {city.name}.")
            self.people[pid] = person
            new_ids.append(pid)
            if person.status > 0.75:
                self.notable.add(pid)

        self._weave_relationships(world, new_ids)

    def _weave_relationships(self, world, ids: list[int]) -> None:
        rng = world.rng.stream("person")
        adults = [i for i in ids if self.people[i].age >= 18]
        rng.shuffle(adults)
        # pair some compatible adults as partners, give them children
        i = 0
        while i + 1 < len(adults):
            a, b = self.people[adults[i]], self.people[adults[i + 1]]
            i += 2
            if a.sex == b.sex or abs(a.age - b.age) > 18 or rng.random() < 0.45:
                continue
            a.partner_id, b.partner_id = b.id, a.id
            a.relate(b.id, "partner", 0.6, "spouse")
            b.relate(a.id, "partner", 0.6, "spouse")
            a.remember(f"I married {b.name}.", "marriage", world.tick - 100, 0.7, [b.id])
            b.remember(f"I married {a.name}.", "marriage", world.tick - 100, 0.7, [a.id])
            # assign young residents as their children
            kids = [k for k in ids if self.people[k].age < min(a.age, b.age) - 16]
            for k in kids[: int(rng.integers(0, 4))]:
                child = self.people[k]
                child.parents = [a.id, b.id]
                a.children.append(k); b.children.append(k)
                for parent in (a, b):
                    parent.relate(k, "family", 0.7, "child")
                child.relate(a.id, "family", 0.6, "parent")
                child.relate(b.id, "family", 0.6, "parent")
        # scatter a few friendships and a rivalry
        for _ in range(len(ids)):
            a = self.people[ids[int(rng.integers(0, len(ids)))]]
            b = self.people[ids[int(rng.integers(0, len(ids)))]]
            if a.id == b.id:
                continue
            if rng.random() < 0.7:
                a.relate(b.id, "friend", 0.3 + 0.3 * rng.random())
                b.relate(a.id, "friend", 0.3 + 0.3 * rng.random())
            else:
                a.relate(b.id, "rival", -0.3 - 0.3 * rng.random(), "old grudge")
                b.relate(a.id, "rival", -0.3 - 0.3 * rng.random(), "old grudge")

    # ------------------------------------------------------------------ tick
    def tick(self, world) -> list[dict]:
        """Advance the lives of active individuals. Cheap: only agents in focused
        (or notable) sets get a life update, and only every LIFE_INTERVAL ticks."""
        if world.tick - self._last_life_tick < LIFE_INTERVAL:
            return []
        self._last_life_tick = world.tick
        events: list[dict] = []
        active = [self.people[pid] for pid in self._active_ids()
                  if self.people.get(pid) and self.people[pid].alive]
        # one batched student forward decides who the liquid net drives this tick
        decisions: dict[int, dict] = {}
        mind = getattr(world, "society_mind", None)
        if mind is not None:
            try:
                decisions = mind.decide_batch(active, world)
            except Exception:  # noqa: BLE001 — the student must never break the sim
                log.exception("society student decide_batch failed")
                decisions = {}
        for p in active:
            events += self._advance_plans(world, p)
            events += self._live_one(world, p, decisions.get(p.id))
        # demote occasionally to stay within budget
        self._enforce_budget()
        return events

    def _active_ids(self) -> list[int]:
        ids = set(self.notable)
        for cid in self.focus_cities:
            for p in self.residents(cid):
                ids.add(p.id)
        return list(ids)

    def _live_one(self, world, p: Person, decision: dict | None = None) -> list[dict]:
        events: list[dict] = []
        p.age += 1
        p.memory.decay()
        city = world.cities.get(p.home_city) if p.home_city else None

        # environmental pressure on health
        if city and city.famine > 0:
            p.health -= 0.08
            p.stress = min(1.0, p.stress + 0.05)
        if city and city.plague > 0:
            p.health -= 0.10
            p.stress = min(1.0, p.stress + 0.07)
        p.health = min(1.0, p.health + 0.03)        # natural recovery
        p.mood = max(-1.0, min(1.0, p.mood + 0.03 * (p.health - 0.5) - 0.04 * p.stress))
        p.stress = max(0.0, p.stress - 0.015)

        # mortality: age + ill health
        death_p = 0.002 + max(0, (p.age - 60)) * 0.006 + max(0, 0.4 - p.health) * 0.3
        if world.rng.stream("agent").random() < death_p:
            return self._die(world, p, city)

        # the liquid student drives this person if the hybrid mind routed them here;
        # otherwise fall back to Level-1 utility cognition (optionally species-biased).
        if decision is not None:
            action = decision["action"]
            p.emotion = decision.get("emotion", p.emotion)
            p.intent = decision.get("intent", p.intent)
            p.mind_source = "student"
        else:
            action = traits.choose_action(p, city, world,
                                          policy_bias=self._policy_bias(world, p))
            if p.mind_source != "teacher":
                p.mind_source = "utility"
        p.last_action = action
        events += self._apply_action(world, p, city, action)
        self._record_experience(world, p, action, city)
        return events

    def _advance_plans(self, world, p: Person) -> list[dict]:
        out: list[dict] = []
        remaining = []
        for plan in p.active_plans:
            plan["progress"] = plan.get("progress", 0) + 1
            if plan["progress"] < plan.get("duration", 3):
                remaining.append(plan)
                continue
            kind = plan.get("kind")
            city = world.cities.get(p.home_city) if p.home_city else None
            if kind == "preach" and city:
                p.ideology["piety"] = min(1.0, p.ideology.get("piety", 0) + 0.15)
                p.remember("I preached a doctrine first spoken by the traveller.", "faith", world.tick, 0.5)
                out.append(self._ev(world, "rumor", f"{p.name} preached in {city.name}",
                                    f"A conversation became public doctrine in {city.name}."))
            elif kind == "recruit" and city:
                p.ideology["radicalism"] = min(1.0, p.ideology.get("radicalism", 0) + 0.12)
                p.grievance = min(1.0, p.grievance + 0.12)
                out.append(self._ev(world, "rumor", f"{p.name} began recruiting",
                                    f"{p.name} quietly tested who would join a movement."))
            elif kind == "migrate":
                out += self._try_migrate(world, p)
            elif kind == "trade" and city:
                p.wealth += 0.8
                city.wealth += 0.5
                out.append(self._ev(world, "trade", f"{p.name} made a profitable bargain",
                                    f"A traveller's words pushed {p.name} toward trade."))
            elif kind == "rebel" and city:
                city.unrest = min(1.0, city.unrest + 0.15)
                p.grievance = min(1.0, p.grievance + 0.2)
                out.append(self._ev(world, "rumor", f"Seditious talk spreads in {city.name}",
                                    f"{p.name} carried a dangerous idea into the streets."))
        p.active_plans = remaining[-8:]
        return out

    def _apply_action(self, world, p, city, action) -> list[dict]:
        out: list[dict] = []
        rng = world.rng.stream("agent")
        if action == "work":
            gain = 0.05 * (0.5 + p.skills.get("trade", 0) + p.skills.get("farming", 0))
            p.wealth += gain
            core = max(p.skills, key=p.skills.get)
            p.skills[core] = min(1.0, p.skills[core] + 0.01)
        elif action == "court" and p.partner_id is None:
            out += self._try_marriage(world, p, city)
        elif action == "feud":
            out += self._try_feud(world, p, city)
        elif action == "migrate" and city:
            out += self._try_migrate(world, p)
        elif action == "study":
            p.skills["scholarship"] = min(1.0, p.skills.get("scholarship", 0) + 0.02)
            if rng.random() < 0.05:
                p.beliefs.append("Knowledge outlasts empires.")
                p.remember("I had a realization while studying.", "achievement",
                           world.tick, 0.4)
        elif action == "socialize":
            self._socialize(world, p)
        elif action == "worship":
            p.remember("I prayed for my family's safety.", "faith", world.tick, 0.2)
        # partnered adults may have a child
        if (p.partner_id and 18 <= p.age <= 45 and p.sex == "f"
                and rng.random() < 0.06):
            out += self._birth(world, p, city)
        # status drifts with wealth
        p.status = min(1.0, 0.7 * p.status + 0.3 * min(1.0, p.wealth / 20))
        return out

    # ---- life events ----
    def _try_marriage(self, world, p, city) -> list[dict]:
        if not city:
            return []
        rng = world.rng.stream("agent")
        cands = [q for q in self.residents(city.id)
                 if q.partner_id is None and q.sex != p.sex
                 and abs(q.age - p.age) < 15 and 16 <= q.age and q.id != p.id]
        if not cands:
            return []
        q = cands[int(rng.integers(0, len(cands)))]
        p.partner_id, q.partner_id = q.id, p.id
        p.relate(q.id, "partner", 0.6, "spouse"); q.relate(p.id, "partner", 0.6, "spouse")
        for a, b in ((p, q), (q, p)):
            a.remember(f"I married {b.name}.", "marriage", world.tick, 0.8, [b.id])
            a.milestones.append(f"Married {b.name}.")
        return [self._ev(world, "social", f"{p.name} married {q.name}",
                         f"In {city.name}, {p.name} and {q.name} were wed.")]

    def _birth(self, world, mother, city) -> list[dict]:
        if len(self.people) >= MAX_PEOPLE:
            return []
        rng = world.rng.stream("person")
        father = self.people.get(mother.partner_id)
        pid = self._nid()
        personality = traits.gen_personality(rng)
        child = Person(
            id=pid, name=traits.gen_name(rng),
            sex="f" if rng.random() < 0.5 else "m", age=0,
            species=mother.species, species_id=mother.species_id,
            civ_id=mother.civ_id, home_city=mother.home_city,
            birthplace=city.name if city else mother.birthplace,
            profession="child", education="none", social_class=mother.social_class,
            personality=personality, goals=traits.goal_weights(personality, rng),
            skills=traits.gen_skills(rng, "child"),
            wealth=0.0, status=mother.status * 0.5, born_tick=world.tick,
            home_building=mother.home_building,
            work_building=self._building_id_for(city, "homes", pid) if city else "",
        )
        child.parents = [mother.id] + ([father.id] if father else [])
        mother.children.append(pid)
        if father:
            father.children.append(pid)
            father.relate(pid, "family", 0.7, "child")
        mother.relate(pid, "family", 0.8, "child")
        child.relate(mother.id, "family", 0.7, "parent")
        self.people[pid] = child
        if city:
            self._occupy_buildings(city, child)
        mother.remember(f"My child {child.name} was born.", "birth", world.tick, 0.9, [pid])
        mother.milestones.append(f"Had a child, {child.name}.")
        return [self._ev(world, "birth", f"{child.name} was born in {child.birthplace}",
                         f"{mother.name} gave birth to {child.name}.")]

    def _try_feud(self, world, p, city) -> list[dict]:
        rivals = [(oid, r) for oid, r in p.relationships.items() if r.strength < 0]
        rng = world.rng.stream("agent")
        if not rivals:
            # make a new rival from a resident
            res = self.residents(city.id) if city else []
            res = [q for q in res if q.id != p.id]
            if not res:
                return []
            q = res[int(rng.integers(0, len(res)))]
            p.relate(q.id, "rival", -0.4, "a bitter quarrel")
            q.relate(p.id, "rival", -0.4, "a bitter quarrel")
            p.remember(f"I fell out with {q.name}.", "conflict", world.tick, -0.5, [q.id])
            return [self._ev(world, "social", f"{p.name} feuds with {q.name}",
                             f"A quarrel set {p.name} against {q.name} in {city.name if city else ''}.")]
        oid, r = rivals[0]
        r.shift(-0.1)
        return []

    def _try_migrate(self, world, p) -> list[dict]:
        # move to another living city of any civ
        dests = [c for c in world.cities.values()
                 if c.alive and c.id != p.home_city and c.famine == 0]
        if not dests:
            return []
        rng = world.rng.stream("agent")
        dest = dests[int(rng.integers(0, len(dests)))]
        old = world.cities.get(p.home_city)
        p.home_city = dest.id
        p.civ_id = dest.civ_id
        p.rootedness = max(0.1, p.rootedness - 0.2)
        p.remember(f"I left {old.name if old else 'home'} for {dest.name}.",
                   "migration", world.tick, -0.3)
        p.milestones.append(f"Migrated to {dest.name}.")
        return [self._ev(world, "migration",
                         f"{p.name} migrated to {dest.name}",
                         f"{p.name} left {old.name if old else 'home'} seeking a better life.")]

    def _socialize(self, world, p) -> None:
        for oid, r in list(p.relationships.items())[:3]:
            r.shift(0.05 if r.kind in ("friend", "partner", "family") else -0.02)

    def _die(self, world, p, city) -> list[dict]:
        p.alive = False
        p.death_tick = world.tick
        cause = ("famine" if city and city.famine > 0 else
                 "plague" if city and city.plague > 0 else
                 "old age" if p.age > 60 else "illness")
        p.death_cause = cause
        self.notable.discard(p.id)
        # --- inheritance (Phase 2): estate passes to spouse, else split among children ---
        estate = p.wealth
        heirs = []
        spouse = self.people.get(p.partner_id) if p.partner_id else None
        if spouse and spouse.alive:
            heirs = [spouse]
        else:
            heirs = [self.people[c] for c in p.children
                     if c in self.people and self.people[c].alive]
        if heirs and estate > 0:
            share = estate / len(heirs)
            for h in heirs:
                h.wealth += share
                # status can rise a notch when inheriting from someone of standing
                if p.status > h.status:
                    h.status = min(1.0, h.status + 0.25 * (p.status - h.status))
                h.milestones.append(f"Inherited {round(share, 1)} from {p.name}.")
                h.remember(f"I inherited from {p.name}.", "achievement",
                           world.tick, 0.2, [p.id])
        p.wealth = 0.0
        # the bereaved remember; descendants keep notable ancestors in memory
        for oid in p.kin():
            o = self.people.get(oid)
            if o and o.alive:
                o.relate(p.id, "", -0.0)
                val = -0.9 if p.status > 0.6 else -0.8
                o.remember(f"{p.name} died of {cause}.", "death", world.tick, val, [p.id])
        notable = p.status > 0.6 or p.age > 70
        return [self._ev(world, "death",
                         f"{p.name} died of {cause}" + (f", aged {p.age}" if notable else ""),
                         f"{p.summary()} has died.",
                         person_id=p.id, city_id=p.home_city,
                         status=round(p.status, 3))] if notable else []

    # ----------------------------------------------------- species learning
    def _policy_bias(self, world, p):
        brain = getattr(world, "species_brain", None)
        if brain is None:
            return None
        city = world.cities.get(p.home_city) if p.home_city else None
        return brain.action_bias(p, city, world)

    def _record_experience(self, world, p, action, city):
        # reward signal for the species policy: thriving = wealth+health+status+kin
        reward = 0.3 * p.health + 0.3 * min(1, p.wealth / 20) + 0.2 * p.status \
                 + 0.2 * min(1, len(p.children) / 3)
        self.experience.append({"species_id": p.species_id, "action": action,
                                 "reward": reward, "features": self.features(p, city, world),
                                 "kind": "individual_thriving",
                                 "tick": world.tick, "person_id": p.id,
                                 "city_id": city.id if city else None})
        if len(self.experience) > 20000:
            self.experience = self.experience[-10000:]

    @staticmethod
    def features(p, city, world=None):
        """Compact numeric state vector for the species policy (see ai/).

        The first 12 dimensions are the original individual state. The remaining
        dimensions ground the policy in social and environmental pressure.
        """
        pers = p.personality
        scarcity = 0.0
        population_pressure = 0.0
        unrest = 0.0
        trade_access = 0.0
        culture = 0.0
        infra = 0.0
        climate_pressure = 0.0
        resource_pressure = 0.0
        war_pressure = 0.0
        biodiversity = 0.5
        if city:
            demand = max(1e-6, city.population * 0.0013)
            scarcity = max(0.0, min(1.0, 1.0 - city.food_production / demand))
            population_pressure = max(0.0, min(1.0, city.population / 25000))
            unrest = max(0.0, min(1.0, city.unrest))
            trade_access = max(0.0, min(1.0, city.wealth / 80))
            culture = max(0.0, min(1.0, city.culture / 120))
            infra = max(0.0, min(1.0, city.infrastructure / 10))
            if world:
                y, x = city.pos
                temp = float(world.temperature[y, x])
                climate_pressure = max(0.0, min(1.0, abs(temp - 18) / 35))
                reg = world.food[max(0, y-3):min(world.height, y+4),
                                 max(0, x-3):min(world.width, x+4)]
                resource_pressure = max(0.0, min(1.0, 1.0 - float(reg.mean())))
                war_pressure = 1.0 if any(u.kind == "army" and u.dest_city == city.id
                                          for u in world.units.values()) else 0.0
                alive = [s.population for s in world.species.values() if s.alive]
                total = sum(alive)
                biodiversity = min(1.0, len([p0 for p0 in alive if p0 > 0]) / 30) if total else 0.0
        ideology = p.ideology or {}
        return [
            pers.get("openness", .5), pers.get("conscientiousness", .5),
            pers.get("extraversion", .5), pers.get("agreeableness", .5),
            pers.get("neuroticism", .5),
            min(1.0, p.age / 80), p.health, min(1.0, p.wealth / 20), p.status,
            1.0 if (city and city.famine > 0) else 0.0,
            1.0 if p.partner_id else 0.0, min(1.0, len(p.children) / 4),
            p.grievance,
            ideology.get("piety", 0.0), ideology.get("radicalism", 0.0),
            ideology.get("militarism", 0.0), ideology.get("mercantilism", 0.0),
            scarcity, population_pressure, unrest, trade_access, culture, infra,
            max(climate_pressure, resource_pressure, war_pressure) * 0.6
            + biodiversity * 0.4,
        ]

    # --------------------------------------------------------- budget / LOD
    def _enforce_budget(self) -> None:
        if len(self.people) <= MAX_PEOPLE:
            return
        droppable = [p for p in self.people.values()
                     if p.alive and p.home_city not in self.focus_cities
                     and p.id not in self.notable]
        droppable.sort(key=lambda p: p.status)        # release least notable first
        # also drop the long dead
        dead = [p for p in self.people.values() if not p.alive]
        to_drop = dead + droppable
        excess = len(self.people) - MAX_PEOPLE
        for p in to_drop[:excess]:
            self.people.pop(p.id, None)

    def unfocus(self, city_id: int) -> None:
        self.focus_cities.discard(city_id)

    def get(self, pid: int) -> Person | None:
        return self.people.get(pid)

    def _ev(self, world, type_, title, detail, **extra):
        return {"tick": world.tick, "type": type_, "title": title,
                "detail": detail, **extra}

    # ---- generators for profile flavor ----
    @staticmethod
    def _class_for(rng, city):
        # culture/wealth lift the ceiling a little, but a realistic society is a broad
        # base of commoners and poor, not a city of nobles. The nudge is capped so a
        # mature, high-culture city doesn't turn everyone into gentry.
        lift = min(0.12, city.culture / 1500) + min(0.06, max(0.0, city.wealth) / 1200)
        # poverty rises with unrest/famine pressure
        squeeze = min(0.12, getattr(city, "unrest", 0.0) * 0.12
                      + (0.06 if city.famine > 0 else 0.0))
        roll = rng.random() + lift - squeeze
        if roll > 0.97: return "noble"
        if roll > 0.90: return "gentry"
        if roll > 0.74: return "merchant"
        if roll > 0.50: return "freeholder"
        if roll > 0.20: return "commoner"
        return "destitute"

    @staticmethod
    def _education(cls, rng):
        levels = {"noble": "tutored", "gentry": "lettered", "merchant": "apprenticed",
                  "freeholder": "apprenticed", "commoner": "rudimentary",
                  "destitute": "none"}
        return levels.get(cls, "rudimentary")

    @staticmethod
    def _wealth_for(cls):
        return {"destitute": 0.3, "commoner": 2, "freeholder": 5,
                "merchant": 12, "gentry": 25, "noble": 60}[cls]

    @staticmethod
    def _beliefs(ident, city, pers, rng):
        civ_name = ident.get("name", "people")
        pool = [f"The {civ_name} are destined to endure.",
                f"{city.name} is the finest city in the world.",
                f"To be {ident.get('ideology', 'free').lower()} is the only true way.",
                "The spirits watch over the harvest.",
                "Strength is the only law.", "Trade binds the world together.",
                "The old ways must be kept.", "Change is the only constant."]
        # the nation's character pushes its own creed forward
        for trait in ident.get("traits", []):
            pool.append(f"A {civ_name[:-1] if civ_name.endswith('s') else civ_name} should be {trait}.")
        for desire in ident.get("desires", []):
            pool.append(f"There is no higher calling than {desire}.")
        n = 1 + int(pers["openness"] * 2)
        return list({pool[int(rng.integers(0, len(pool)))] for _ in range(n)})

    @staticmethod
    def _profession_pool(city, ident) -> list[str]:
        """Professions weighted by city geography AND national character, so a
        militarist warhost fields soldiers and a mercantile league fields traders."""
        weights: dict[str, float] = {}
        for prof, spec in traits.PROFESSIONS.items():
            w = 1.0
            if spec == city.specialty:
                w += 2.0
            if prof in ("soldier", "hunter"):
                w += 2.5 * ident.get("military", 0.5)
            if prof in ("trader", "sailor", "fisher"):
                w += 2.5 * ident.get("economic", 0.5)
            if prof in ("priest",):
                w += 3.0 * ident.get("religious", 0.5)
            if prof in ("scholar", "scribe"):
                w += 2.0 * ident.get("exploration", 0.5)
            weights[prof] = w
        # expand to a weighted list (integerized) the caller samples uniformly
        pool: list[str] = []
        for prof, w in weights.items():
            pool.extend([prof] * max(1, int(round(w * 2))))
        return pool or list(traits.PROFESSIONS)

    @staticmethod
    def _ideology_for(ident, personality, rng) -> dict[str, float]:
        """A citizen's ideology is their nation's axes, pulled toward their own
        personality and jittered — so a nation is recognizable but never uniform."""
        from ..society import beliefs as _b
        axes = ident.get("axes", {})
        out: dict[str, float] = {}
        for axis in _b.AXES:
            national = float(axes.get(axis, 0.5))
            jitter = float(rng.normal(0, 0.16))
            personal = 0.0
            if axis == "piety":
                personal = 0.3 * personality.get("neuroticism", .5)
            elif axis == "radicalism":
                personal = 0.3 * (1 - personality.get("agreeableness", .5))
            elif axis == "traditionalism":
                personal = 0.3 * (1 - personality.get("openness", .5))
            out[axis] = round(float(np.clip(0.65 * national + 0.35 * personal + jitter,
                                            0.0, 1.0)), 3)
        return out

    @staticmethod
    def _grievance_for(city, cls, ideology, personality, rng) -> float:
        base = (0.35 if cls == "destitute" else 0.18 if cls == "commoner" else 0.05)
        base += getattr(city, "unrest", 0.0) * 0.3
        base += ideology.get("radicalism", 0.0) * 0.2
        base += personality.get("neuroticism", 0.5) * 0.12
        return round(float(np.clip(base + rng.normal(0, 0.08), 0.0, 1.0)), 3)

    @staticmethod
    def _life_goal(ident, personality, rng) -> str:
        desires = ident.get("desires", [])
        pool = ["leave a name that outlives them", "keep their family safe and fed",
                "rise above the station they were born to", "see the edge of the world",
                "master their craft", "win the love they were denied",
                "avenge an old wrong", "find peace with their past"]
        if desires:
            pool += [f"serve the cause of {d}" for d in desires]
        if personality.get("openness", .5) > 0.6:
            pool.append("understand why the world is the way it is")
        return pool[int(rng.integers(0, len(pool)))]

    @staticmethod
    def _pick_one(rng, pool):
        return pool[int(rng.integers(0, len(pool)))]

    @staticmethod
    def _fears(city, rng):
        pool = ["famine", "war", "the sea", "plague", "dishonor", "the dark",
                "being forgotten", "outsiders"]
        return list({pool[int(rng.integers(0, len(pool)))] for _ in range(2)})

    @staticmethod
    def _prefs(rng):
        pool = ["music", "the hunt", "fine food", "solitude", "festivals",
                "the open road", "craftwork", "stories", "drink", "gardens"]
        return list({pool[int(rng.integers(0, len(pool)))] for _ in range(2)})

    @staticmethod
    def _possessions_for(cls, profession):
        base = {"clothes": 1.0, "tools": 0.4}
        if cls in ("merchant", "gentry", "noble"):
            base["silver"] = {"merchant": 8, "gentry": 15, "noble": 40}[cls]
        if profession in ("soldier", "hunter"):
            base["weapon"] = 1.0
        if profession in ("farmer", "fisher", "trader", "smith", "miner", "artisan"):
            base["trade_goods"] = 1.0
        if profession in ("scholar", "scribe", "priest"):
            base["books"] = 1.0
        return base

    @staticmethod
    def _ambitions(personality, rng):
        pool = ["be remembered", "protect my family", "grow wealthy",
                "see another city", "earn respect", "learn a hidden truth",
                "serve the gods", "escape my station"]
        n = 1 + int(personality.get("openness", 0.5) > 0.62)
        return list({pool[int(rng.integers(0, len(pool)))] for _ in range(n)})

    @staticmethod
    def _home_kind_for(cls):
        return {"destitute": "slums", "commoner": "homes", "freeholder": "homes",
                "merchant": "homes", "gentry": "noble_district",
                "noble": "noble_district"}[cls]

    @staticmethod
    def _work_kind_for(profession):
        return {"farmer": "farms", "fisher": "docks", "sailor": "docks",
                "trader": "market", "merchant": "market",
                "smith": "workshops", "artisan": "workshops", "miner": "mines",
                "soldier": "barracks", "priest": "temples",
                "scholar": "archives", "scribe": "archives",
                "healer": "tavern", "laborer": "workshops",
                "builder": "workshops", "child": "homes"}.get(profession, "market")

    @staticmethod
    def _building_id_for(city, kind: str, person_id: int) -> str:
        entities = getattr(city, "building_entities", {}) or {}
        ids = sorted(bid for bid, b in entities.items()
                     if not getattr(b, "abandoned", False) and getattr(b, "kind", "") == kind)
        if not ids and kind != "homes":
            ids = sorted(bid for bid, b in entities.items()
                         if not getattr(b, "abandoned", False) and getattr(b, "kind", "") == "homes")
        if not ids:
            return kind
        return ids[person_id % len(ids)]

    @staticmethod
    def _occupy_buildings(city, person: Person) -> None:
        entities = getattr(city, "building_entities", {}) or {}
        for bid, role in ((person.home_building, "owner"),
                          (person.work_building, "worker")):
            b = entities.get(bid)
            if not b:
                continue
            if role == "owner" and person.social_class in ("merchant", "gentry", "noble"):
                b.owner_id = person.id
            if role == "worker" and len(b.workers) < 12:
                b.workers.append(person.id)
