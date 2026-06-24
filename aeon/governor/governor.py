"""The governor loop: the world-spirit's deliberation cycle.

Every `governor.tick_seconds` it:
  1. asks telemetry for a compact stats snapshot,
  2. builds a prompt (current stats + memory + recent history),
  3. calls the LLM,
  4. parses the JSON, validates each directive, applies the safe ones,
  5. records its thought, goal, and any myth into memory.

It deliberately runs on its own slow clock, decoupled from the fast sim tick, so a
slow model never stalls the world. Crucially it only ever calls into
`directives.apply` and reads `stats` — it cannot touch the grids.
"""

from __future__ import annotations

import logging

from . import prompts
from .directives import Directive, apply as apply_directive
from .llm import LLMClient
from .memory import GovernorMemory

log = logging.getLogger("aeon.governor")


class Governor:
    def __init__(self, cfg, world, memory: GovernorMemory, history, metrics):
        self.cfg = cfg
        self.world = world
        self.memory = memory
        self.history = history          # telemetry.history.History
        self.metrics = metrics          # telemetry.metrics.Metrics
        self.llm = LLMClient(cfg)
        self.last_thought = ""
        self.last_directives: list[dict] = []
        self.deliberations = 0

    async def deliberate(self, stats: dict) -> dict:
        """Run one full think→act cycle. Returns a record for the dashboard."""
        self.deliberations += 1
        recent = self.history.recent(10)
        system = prompts.SYSTEM
        user = prompts.tick_prompt(stats, self.memory.summary_for_prompt(), recent)

        raw = await self.llm.complete(system, user)
        parsed = prompts.parse_response(raw)

        thought = parsed.get("thought", "")
        applied: list[dict] = []
        for raw_dir in parsed.get("directives", [])[:4]:
            d = Directive.parse(raw_dir)
            if d is None:
                continue
            res = apply_directive(self.world, self.memory, d)
            applied.append({"op": d.op, "reason": d.reason,
                            "ok": res.ok, "message": res.message})
            if res.ok:
                self.history.add({"tick": self.world.tick, "type": "governor",
                                  "title": f"Spirit: {res.message}",
                                  "detail": d.reason})

        if parsed.get("goal"):
            self.memory.set_goal(parsed["goal"], thought)
        myth = parsed.get("myth")
        if myth and not isinstance(myth, dict):
            myth = None
        if myth and myth.get("text"):
            self.memory.add_myth(myth.get("title", "Untitled"), myth["text"],
                                 self.world.tick)

        self.memory.record_decision(
            self.world.tick, thought,
            [a["message"] for a in applied if a["ok"]], thought)
        self.last_thought = thought
        self.last_directives = applied

        return {"thought": thought, "goal": self.memory.current_goal,
                "directives": applied, "online": self.llm.online,
                "tick": self.world.tick}

    async def aclose(self) -> None:
        await self.llm.aclose()
