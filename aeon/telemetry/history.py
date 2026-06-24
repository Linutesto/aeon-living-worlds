"""The historical timeline: an append-only log of notable world events.

Events are plain dicts with at least {tick, type, title, detail}. `type` is one of:
extinction, speciation, civilization, settlement, war, collapse, event, event_end,
governor. The dashboard filters by type; the governor reads the recent slice.
"""

from __future__ import annotations

from collections import deque


class History:
    def __init__(self, max_events: int = 5000) -> None:
        self._events: "deque[dict]" = deque(maxlen=max_events)
        self._seq = 0

    def add(self, event: dict) -> dict:
        self._seq += 1
        event = {"id": self._seq, **event}
        self._events.append(event)
        return event

    def extend(self, events: list[dict]) -> None:
        for e in events:
            self.add(e)

    def recent(self, n: int = 20) -> list[dict]:
        return list(self._events)[-n:]

    def since_id(self, last_id: int) -> list[dict]:
        return [e for e in self._events if e["id"] > last_id]

    def filter(self, type: str | None = None, limit: int = 200) -> list[dict]:
        items = (e for e in reversed(self._events)
                 if type is None or e.get("type") == type)
        out = []
        for e in items:
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def count_since(self, tick: int, type: str | None = None) -> int:
        return sum(1 for e in self._events
                   if e["tick"] >= tick and (type is None or e.get("type") == type))
