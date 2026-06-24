"""Observer influence: how the user's words become part of history."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ObserverState:
    reputation: float = 0.0
    influence: float = 0.0
    persona: str = "unknown spirit"
    interventions: list[dict] = field(default_factory=list)
    relationships: dict[int, float] = field(default_factory=dict)
    myths: list[str] = field(default_factory=list)

    def record(self, tick: int, person_id: int, effect: str, text: str) -> None:
        self.influence = min(1.0, self.influence + 0.02)
        self.reputation = max(-1.0, min(1.0, self.reputation + 0.01))
        self.relationships[person_id] = max(-1.0, min(1.0,
            self.relationships.get(person_id, 0.0) + 0.04))
        self.interventions.append({"tick": tick, "person_id": person_id,
                                   "effect": effect, "text": text[:160]})
        self.interventions = self.interventions[-200:]
        if self.influence > 0.6:
            self.persona = "legend"
        elif self.influence > 0.35:
            self.persona = "reformer"
        elif self.influence > 0.15:
            self.persona = "whispering patron"
