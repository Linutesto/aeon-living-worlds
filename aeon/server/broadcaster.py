"""Pushes light live state to all connected WebSocket clients.

Omega renderer terrain/buildings/citizens are streamed through /api/render/chunk.
The websocket stays lightweight: vitals, city summaries, live movers, society,
governor state, memory, metrics, and wildlife.
"""

from __future__ import annotations

import asyncio
import logging

from .encoding import to_jsonable

log = logging.getLogger("aeon.broadcaster")


class Broadcaster:
    def __init__(self, engine, cfg) -> None:
        self.engine = engine
        self.cfg = cfg
        self.clients: set = set()
        self._task: asyncio.Task | None = None
        self._cycle = 0

    def register(self, ws) -> None:
        self.clients.add(ws)

    def unregister(self, ws) -> None:
        self.clients.discard(ws)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="broadcaster")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        interval = 1.0 / max(0.5, self.cfg.broadcast_hz)
        while True:
            await asyncio.sleep(interval)
            if not self.clients:
                continue
            self._cycle += 1
            c = self._cycle
            # high rate: units + markers + headline vitals → smooth motion
            payloads = [self.engine.serialize_live(), self.engine.serialize_overview()]
            if c % 3 == 0:                      # ~4Hz: city economy + routes
                payloads.append(self.engine.serialize_cities())
            if c % 6 == 0:                      # ~2Hz: governor mind
                payloads.append(self.engine.serialize_governor())
            if c % 12 == 1:                     # ~1Hz: charts, memory, ecology, society
                payloads.append(self.engine.serialize_metrics())
                payloads.append(self.engine.serialize_memory())
                payloads.append(self.engine.serialize_wildlife())
                payloads.append(self.engine.serialize_society())
            await self._send_all(payloads)

    async def _send_all(self, payloads: list[dict]) -> None:
        # sanitize once per cycle, not once per client
        clean = [to_jsonable(p) for p in payloads]
        dead = []
        for ws in list(self.clients):
            try:
                for p in clean:
                    await ws.send_json(p)
            except Exception:  # noqa: BLE001 — client vanished
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)
