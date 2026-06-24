"""The Person — one real, persistent individual.

A Person owns everything the spec demands of an individual: a profile, a Big-Five
personality with values/motivations, decaying memory, evolving relationships, shifting
goals, beliefs, skills, fears/preferences, and a life history. Behavior is produced by
traits.py (Level-1 utility cognition); this module is the record those systems read
and write, and the thing the dashboard inspects and the LLM speaks *as*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .memory import MemoryStore


@dataclass
class Relationship:
    other_id: int
    kind: str                  # family, friend, partner, rival, enemy, mentor
    strength: float = 0.0      # -1 (hatred) .. +1 (devotion)
    note: str = ""

    def shift(self, d: float) -> None:
        self.strength = max(-1.0, min(1.0, self.strength + d))


@dataclass
class Person:
    id: int
    name: str
    sex: str                   # "f" / "m"
    age: int
    species: str               # culture/lineage; keys the species neural policy
    species_id: int
    civ_id: int
    home_city: int | None
    birthplace: str
    profession: str
    education: str
    social_class: str

    personality: dict[str, float] = field(default_factory=dict)   # Big Five
    values: dict[str, float] = field(default_factory=dict)
    goals: dict[str, float] = field(default_factory=dict)          # goal -> weight
    skills: dict[str, float] = field(default_factory=dict)
    beliefs: list[str] = field(default_factory=list)
    fears: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)

    # the inner life that drives emergent society (society/ package)
    ideology: dict[str, float] = field(default_factory=dict)  # piety, radicalism, …
    grievance: float = 0.0                 # accumulated resentment (0..1)
    religion_id: int | None = None
    faction_ids: list[int] = field(default_factory=list)

    # --- individuating colour: what makes this person not interchangeable ---
    quirk: str = ""                    # a memorable personality tic
    speech_style: str = ""             # how they talk (terse, florid, blunt…)
    life_goal: str = ""                # the one thing they want from life
    personal_problem: str = ""         # the trouble they carry
    past_event: str = ""               # a formative memory headline
    civ_loyalty: float = 0.5           # attachment to their nation (0..1)
    religion_loyalty: float = 0.0      # attachment to their faith (0..1)
    class_tension: float = 0.0         # resentment of / pressure from their station
    local_identity: str = ""           # pride/identity tied to the home city

    relationships: dict[int, Relationship] = field(default_factory=dict)
    partner_id: int | None = None
    parents: list[int] = field(default_factory=list)
    children: list[int] = field(default_factory=list)

    wealth: float = 1.0
    health: float = 1.0
    rootedness: float = 0.5    # attachment to home; high = unlikely to migrate
    status: float = 0.2        # social standing 0..1
    mood: float = 0.0          # -1 despair .. +1 elation
    stress: float = 0.0        # 0..1 pressure
    trust_observer: float = 0.0
    reputation: float = 0.0
    alive: bool = True
    born_tick: int = 0
    death_tick: int | None = None
    death_cause: str = ""

    memory: MemoryStore = field(default_factory=MemoryStore)
    milestones: list[str] = field(default_factory=list)   # life history headlines
    possessions: dict[str, float] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list)
    rumors: list[str] = field(default_factory=list)
    ambitions: list[str] = field(default_factory=list)
    active_plans: list[dict] = field(default_factory=list)
    home_building: str = ""
    work_building: str = ""
    last_action: str = ""
    born_real: bool = False    # materialized vs implied (for LOD bookkeeping)

    # inner life written by the Society Intelligence Stack (aeon/mind/)
    emotion: str = ""          # teacher/student read of current feeling
    intent: str = ""           # future_intent the mind assigns
    last_dialogue: str = ""    # most recent line the mind had them say
    mind_source: str = "utility"   # which cognition drove them: utility|student|teacher

    # ---- relationship helpers ----
    def relate(self, other_id: int, kind: str, d: float, note: str = "") -> None:
        r = self.relationships.get(other_id)
        if r is None:
            r = Relationship(other_id, kind, 0.0, note)
            self.relationships[other_id] = r
        if kind:
            r.kind = kind
        if note:
            r.note = note
        r.shift(d)

    def remember(self, text, kind, tick, valence=0.0, subjects=None):
        return self.memory.add(text, kind, tick, valence, subjects)

    def kin(self) -> list[int]:
        return self.parents + self.children + (
            [self.partner_id] if self.partner_id else [])

    def dominant_goal(self) -> str:
        return max(self.goals, key=self.goals.get) if self.goals else "survive"

    def summary(self) -> str:
        """One-line identity used in lists and prompts."""
        return (f"{self.name}, {self.age}-year-old {self.sex} {self.profession} "
                f"of {self.birthplace} ({self.social_class})")
