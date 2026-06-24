"""The Chronicle — the world's history book, written on demand by the local LLM.

The simulation produces a stream of events; the overwhelming majority are logged
cheaply to the timeline. But when something *historic* happens — a faith is founded,
a schism tears it, a revolution topples a state, a holy war begins, a civilization
falls — that event is queued here and the local model is asked, ONCE, to set it down
as a chronicler would: a few sentences of legend and consequence. This is the
event-driven LLM layer: rich language at the moments that deserve it, never per-tick.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Entry:
    tick: int
    kind: str
    title: str
    text: str


class Chronicle:
    def __init__(self, capacity: int = 400) -> None:
        self.entries: "deque[Entry]" = deque(maxlen=capacity)
        self._seq = 0

    def add(self, tick, kind, title, text) -> Entry:
        e = Entry(tick=tick, kind=kind, title=title, text=text.strip())
        self.entries.append(e)
        self._seq += 1
        return e

    def recent(self, n: int = 50) -> list[dict]:
        return [asdict(e) for e in list(self.entries)[-n:][::-1]]

    def save(self, path) -> None:
        Path(path).write_text(json.dumps([asdict(e) for e in self.entries], indent=2))

    def load(self, path) -> None:
        p = Path(path)
        if p.exists():
            for d in json.loads(p.read_text()):
                self.entries.append(Entry(**d))


SYSTEM = """You are the Chronicler of a living world — a historian setting down its
great events as they happen. Write 2–4 sentences in the cadence of a history book or
a legend: vivid, grounded, and consequential. Name the people, faiths, factions, and
cities given to you. Do not invent contradicting facts, do not use modern words, and
never mention that this is a simulation."""


def build_prompt(event: dict, world) -> str:
    era = f"the {1 + world.tick // 1000}th age (year {world.tick})"
    return (f"In {era}, this came to pass:\n"
            f"TITLE: {event.get('title','')}\n"
            f"WHAT HAPPENED: {event.get('detail','')}\n"
            f"KIND: {event.get('type','')}\n\n"
            f"Write the chronicle entry for this event:")
