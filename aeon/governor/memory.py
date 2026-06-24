"""The world-spirit's long-term memory: myths, goals, decisions, philosophy.

This is what gives the world a persistent narrative. It is fed back into every
governor prompt (compactly) so the spirit stays coherent across deliberations, and
it is surfaced wholesale in the dashboard's World Memory and AI Governor panels.

Kept in-memory with a JSON snapshot for persistence across restarts. A real
deployment might swap this for SQLite; the interface would not change.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Myth:
    title: str
    text: str
    tick: int


@dataclass
class Decision:
    tick: int
    thought: str
    directives: list[str]
    reason: str


@dataclass
class GovernorMemory:
    philosophy: str = ("I am the spirit of this world. I do not command its "
                       "creatures; I shape the pressures they live under, and I "
                       "tell the story of what becomes of them.")
    current_goal: str = "Cultivate a living, surprising, biodiverse world."
    goal_reason: str = ""
    goals_history: list[str] = field(default_factory=list)
    myths: list[Myth] = field(default_factory=list)
    decisions: "deque[Decision]" = field(default_factory=lambda: deque(maxlen=200))
    beliefs: dict[str, str] = field(default_factory=dict)

    # ---- mutation API used by directives + governor loop ----
    def set_goal(self, goal: str, reason: str = "") -> None:
        if goal and goal != self.current_goal:
            self.goals_history.append(self.current_goal)
            self.current_goal = goal
            self.goal_reason = reason

    def add_myth(self, title: str, text: str, tick: int = 0) -> None:
        self.myths.append(Myth(title=title, text=text, tick=tick))

    def record_decision(self, tick, thought, directives, reason="") -> None:
        self.decisions.append(Decision(tick, thought, list(directives), reason))

    def record_event(self, ev: dict) -> None:
        # events the spirit itself triggered are worth remembering as beliefs
        self.beliefs[f"event:{ev.get('kind','?')}"] = ev.get("detail", "")

    # ---- read API for prompts + dashboard ----
    def recent_decisions(self, n: int = 5) -> list[dict]:
        return [asdict(d) for d in list(self.decisions)[-n:]]

    def recent_myths(self, n: int = 5) -> list[dict]:
        return [asdict(m) for m in self.myths[-n:]]

    def summary_for_prompt(self) -> str:
        myths = "; ".join(m.title for m in self.myths[-3:]) or "none yet"
        return (f"Philosophy: {self.philosophy}\n"
                f"Current goal: {self.current_goal}\n"
                f"Recent myths: {myths}")

    # ---- persistence ----
    def save(self, path: str | Path) -> None:
        data = {
            "philosophy": self.philosophy,
            "current_goal": self.current_goal,
            "goal_reason": self.goal_reason,
            "goals_history": self.goals_history,
            "myths": [asdict(m) for m in self.myths],
            "decisions": [asdict(d) for d in self.decisions],
            "beliefs": self.beliefs,
        }
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "GovernorMemory":
        p = Path(path)
        if not p.exists():
            return cls()
        d = json.loads(p.read_text())
        m = cls(
            philosophy=d.get("philosophy", cls.philosophy),
            current_goal=d.get("current_goal", ""),
            goal_reason=d.get("goal_reason", ""),
            goals_history=d.get("goals_history", []),
            beliefs=d.get("beliefs", {}),
        )
        m.myths = [Myth(**x) for x in d.get("myths", [])]
        for x in d.get("decisions", []):
            m.decisions.append(Decision(**x))
        return m
