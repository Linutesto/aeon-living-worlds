"""Episodic memory that decays — important memories survive longer.

Each memory carries a *salience* that decays a little every life-tick. Emotional
intensity (how good/bad it was) makes a memory more salient and slower to fade.
Recall (or a related new event) reinforces it. When a person's store is full, the
faintest memories are forgotten first — so a life is remembered by its peaks: the
losses, the triumphs, the people who mattered.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Memory:
    text: str
    kind: str                  # birth, death, marriage, conflict, achievement, ...
    tick: int
    salience: float = 1.0      # current strength of the memory (0..~3)
    valence: float = 0.0       # -1 (traumatic) .. +1 (joyful)
    subjects: list[int] = field(default_factory=list)   # related person ids

    def reinforce(self, amount: float = 0.4) -> None:
        self.salience = min(3.0, self.salience + amount)


class MemoryStore:
    def __init__(self, capacity: int = 40) -> None:
        self.capacity = capacity
        self.items: list[Memory] = []

    def add(self, text, kind, tick, valence=0.0, subjects=None) -> Memory:
        # base salience rises with emotional intensity
        m = Memory(text=text, kind=kind, tick=tick,
                   salience=1.0 + abs(valence), valence=valence,
                   subjects=subjects or [])
        self.items.append(m)
        if len(self.items) > self.capacity:
            self._forget()
        return m

    def decay(self, rate: float = 0.985) -> None:
        """Fade all memories slightly. Vivid (high-valence) ones resist decay."""
        for m in self.items:
            resist = 1.0 - 0.5 * min(1.0, abs(m.valence))
            m.salience *= (rate + (1 - rate) * (1 - resist))
        self.items = [m for m in self.items if m.salience > 0.08]

    def _forget(self) -> None:
        self.items.sort(key=lambda m: m.salience, reverse=True)
        self.items = self.items[: self.capacity]

    def top(self, n: int = 8) -> list[Memory]:
        return sorted(self.items, key=lambda m: m.salience, reverse=True)[:n]

    def about(self, person_id: int) -> list[Memory]:
        return [m for m in self.items if person_id in m.subjects]

    def __len__(self) -> int:
        return len(self.items)
