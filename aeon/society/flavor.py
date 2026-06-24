"""Local-LLM world flavor — rumors, news, journals, sermons, obituaries, letters.

This enriches *moments*, never every citizen every tick. The engine's flavor loop
picks one eventful subject every few seconds, asks the local model for a short piece,
and caches it. Generation is:

  * event-driven  — subjects are chosen from recent history / focused cities,
  * async          — runs on its own loop, off the simulation hot path,
  * rate-limited   — at most one piece per `min_interval` seconds,
  * cached         — stored here and persisted with the world,
  * bounded        — per-city ring buffers + a global feed, capped.

Nothing here blocks the simulation; if the model is unreachable the store simply
stops growing.
"""

from __future__ import annotations

import json
import random
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

# kind -> (system persona, how to phrase the ask). Kept short; the model is small.
KINDS = {
    "rumor":      "a whispered rumor passing through the streets",
    "news":       "a short item of city news, as a town crier would cry it",
    "journal":    "a private journal entry by an ordinary citizen",
    "gossip":     "marketplace gossip traded between strangers",
    "sermon":     "a few lines from a street preacher's sermon",
    "propaganda": "a faction's propaganda handbill",
    "dream":      "a citizen's strange dream, told on waking",
    "letter":     "a brief letter sent from this city to a distant friend",
    "obituary":   "a short obituary remembering one who has died",
    "flavor":     "a sensory vignette of daily life in this place",
}

SYSTEM = ("You give voice to a single small moment in a living medieval-fantasy world. "
          "Write 1-3 sentences, vivid and grounded, in period voice. Never mention that "
          "this is a game or simulation. Use only the details you are given.")


@dataclass
class FlavorPiece:
    tick: int
    kind: str
    city_id: int | None
    city: str
    text: str


class FlavorStore:
    def __init__(self, per_city: int = 12, feed_max: int = 120) -> None:
        self.per_city = per_city
        self.by_city: dict[int, deque] = {}
        self.feed: deque = deque(maxlen=feed_max)

    def add(self, piece: FlavorPiece) -> None:
        if piece.city_id is not None:
            buf = self.by_city.setdefault(piece.city_id, deque(maxlen=self.per_city))
            buf.append(piece)
        self.feed.append(piece)

    def for_city(self, city_id: int, n: int = 8) -> list[dict]:
        buf = self.by_city.get(city_id)
        return [asdict(p) for p in list(buf)[-n:][::-1]] if buf else []

    def recent(self, n: int = 30) -> list[dict]:
        return [asdict(p) for p in list(self.feed)[-n:][::-1]]

    # ---- persistence ----
    def save(self, path) -> None:
        Path(path).write_text(json.dumps([asdict(p) for p in self.feed]))

    def load(self, path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            for d in json.loads(p.read_text()):
                self.add(FlavorPiece(**d))
        except Exception:  # noqa: BLE001 — corrupt cache is non-fatal
            pass


def pick_subject(engine, rng: random.Random):
    """Choose (city, kind) to flavor — biased toward focused and eventful cities."""
    world = engine.world
    live = [c for c in world.cities.values() if c.alive]
    if not live:
        return None, None
    focused = [c for c in live if c.id in engine.population.focus_cities]
    pool = focused * 3 + live                      # weight focused cities heavily
    city = rng.choice(pool)
    # pick a kind that fits the city's state
    if city.famine > 0 and rng.random() < 0.4:
        kind = rng.choice(["rumor", "journal", "sermon"])
    elif city.unrest > 0.5 and rng.random() < 0.4:
        kind = rng.choice(["propaganda", "gossip", "news"])
    else:
        kind = rng.choice(list(KINDS))
    return city, kind


def build_prompt(engine, city, kind: str) -> str:
    world = engine.world
    civ = world.civilizations.get(city.civ_id)
    religion, _ = engine.society.religion_of_city(city.id)
    state = []
    if city.famine > 0: state.append("famine")
    if city.plague > 0: state.append("plague")
    if city.unrest > 0.5: state.append("unrest")
    state_txt = ", ".join(state) or "an uneasy peace"
    return (f"Place: {city.name}, a {city.tier} known as a {city.specialty.lower()}, "
            f"home to the {civ.name if civ else 'free folk'}. "
            f"Faith: {religion.name if religion else 'old local spirits'}. "
            f"Mood: {state_txt}. "
            f"Write {KINDS[kind]} for this place. Keep it to 1-3 sentences.")
