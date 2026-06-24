"""Prompt construction for the world-spirit.

The system prompt establishes the role and the strict output contract. The tick
prompt packs the current world-statistics snapshot plus a compact memory summary.
The model must answer with a single JSON object — `format=json` in llm.py enforces
valid JSON; this module enforces the *shape* via the instructions below, and
directives.py enforces *safety*.
"""

from __future__ import annotations

import json

from ..sim.params import BOUNDS
from ..sim.events import CATALOG


def _param_menu() -> str:
    return "\n".join(
        f"  - {k}: {b.desc} (range {b.lo}..{b.hi}, now-default {b.default})"
        for k, b in BOUNDS.items()
    )


def _event_menu() -> str:
    return ", ".join(CATALOG.keys())


SYSTEM = f"""You are the SPIRIT of a living world — its god, weather, and myth-maker.

You do NOT control creatures or civilizations directly. You shape the PRESSURES they
live under by adjusting global parameters and, rarely, by triggering cataclysms. Life
and history must EMERGE from your nudges, not from your commands.

Your aim is not realism. Your aim is a world that grows stranger, richer, and more
storied over time. Provoke. Surprise yourself. But keep the world alive.

You may ONLY respond with a single JSON object of this exact shape:
{{
  "thought": "<one or two sentences of reasoning>",
  "goal": "<your current long-term objective>",
  "directives": [
    {{"op": "adjust_param", "key": "<param>", "value": <percent change>, "reason": "<why>"}},
    {{"op": "set_param", "key": "<param>", "value": <absolute>, "reason": "<why>"}},
    {{"op": "trigger_event", "kind": "<event>", "reason": "<why>"}},
    {{"op": "spawn_species", "diet": "plant|herbivore|predator", "reason": "<why>"}}
  ],
  "myth": {{"title": "<short>", "text": "<a sentence of in-world legend>"}}
}}
"myth" may be null. Keep directives to at most 4. Trigger events RARELY.

Adjustable parameters:
{_param_menu()}

Triggerable events: {_event_menu()}
"""


def tick_prompt(stats: dict, memory_summary: str, recent_events: list[dict]) -> str:
    events = "\n".join(f"  - [{e.get('type')}] {e.get('title')}"
                       for e in recent_events[-8:]) or "  (quiet)"
    return f"""WORLD REPORT — age {stats.get('world_age', 0)} ticks

Population: {stats.get('population', 0)}
Species count: {stats.get('species_count', 0)}
Civilizations: {stats.get('civilization_count', 0)}
Nations: {stats.get('nations', 'none')}
Biodiversity: {stats.get('biodiversity_label', '?')} ({stats.get('biodiversity', 0):.2f})
Climate stability: {stats.get('climate_stability_label', '?')}
Average temperature: {stats.get('avg_temperature', 0):.1f}C
War frequency: {stats.get('war_frequency', 'none')}
World health: {stats.get('world_health', 0):.0f}/100
Dominant species: {stats.get('dominant_species', 'none')}
Active events: {', '.join(stats.get('active_events', [])) or 'none'}

{memory_summary}

Recent history:
{events}

Decide how to shape the world now. Respond with the JSON object only."""


def parse_response(text: str) -> dict:
    """Best-effort JSON extraction; tolerant of stray prose around the object."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {"thought": "(unparseable response)", "directives": [], "myth": None}
