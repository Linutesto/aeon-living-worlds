"""TeacherInference — one 27B call reasons over a cohort; outputs become training data.

Flow: pick a cohort → compress to one prompt → call the (big) model → tolerantly parse
a per-citizen decision → *advisorily* apply it to each Person's inner life (emotion,
memory, intent, spoken line, action intent) → log one canonical training sample per
citizen on the "behavior" channel.

The teacher does NOT run the sim's economic/event mechanics — the deterministic
agents/sim life-tick remains authoritative over outcomes (AGENTS.md invariant). The
teacher enriches what a person feels/remembers/wants and provides the labels the
student learns from.
"""

from __future__ import annotations

import json
import logging
import re

from .cohort import CohortBatcher, Cohort, world_state
from .dataset import Sample, SocietyDataset
from .encode import (ACTIONS, EMOTIONS, INTENTS, get_embedder,
                     action_index, emotion_index, intent_index)

log = logging.getLogger("aeon.mind.teacher")

# emotion → mood nudge (the teacher's read of feeling gently colors the sim mood)
_EMOTION_MOOD = {
    "content": 0.2, "joyful": 0.6, "hopeful": 0.4, "proud": 0.4, "anxious": -0.2,
    "fearful": -0.4, "angry": -0.3, "resentful": -0.4, "grieving": -0.6, "numb": -0.1,
}
_OBJ_RE = re.compile(r"\{[^{}]*\"id\"\s*:\s*\d+[^{}]*\}")


def _parse(raw: str) -> list[dict]:
    """Best-effort: prefer well-formed JSON, fall back to scraping citizen objects."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        cits = data.get("citizens") if isinstance(data, dict) else data
        if isinstance(cits, list):
            return [c for c in cits if isinstance(c, dict) and "id" in c]
    except (json.JSONDecodeError, AttributeError):
        pass
    out = []
    for m in _OBJ_RE.finditer(raw):
        try:
            obj = json.loads(m.group(0))
            if "id" in obj:
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def _clip(value, vocab: list[str], default: str) -> str:
    return value if value in vocab else default


class TeacherInference:
    def __init__(self, llm, dataset: SocietyDataset, *, batcher: CohortBatcher | None = None,
                 embedder=None, model: str = "teacher") -> None:
        self.llm = llm
        self.dataset = dataset
        self.batcher = batcher or CohortBatcher()
        self.embedder = embedder or get_embedder()
        self.model = model
        self.cohorts_run = 0
        self.citizens_taught = 0
        self.last_reason = ""

    async def run(self, world, population, society=None, rng=None) -> dict:
        cohort = self.batcher.pick(world, population, society, rng=rng)
        if cohort is None:
            return {"ran": False, "reason": "no-cohort"}
        system, user = self.batcher.build_prompt(world, cohort, society)
        raw = await self.llm.complete(system, user, format_json=True)
        decisions = _parse(raw)
        applied = self.apply(world, cohort, decisions, society)
        self.cohorts_run += 1
        self.citizens_taught += applied
        self.last_reason = cohort.reason
        return {"ran": True, "city": cohort.city_name, "reason": cohort.reason,
                "cohort": len(cohort.persons), "parsed": len(decisions),
                "applied": applied}

    def apply(self, world, cohort: Cohort, decisions: list[dict], society=None) -> int:
        by_id = {p.id: p for p in cohort.persons}
        ws = world_state(world, society)
        city = world.cities.get(cohort.city_id) if cohort.city_id is not None else None
        applied = 0
        for dec in decisions:
            try:
                pid = int(dec["id"])
            except (KeyError, ValueError, TypeError):
                continue
            p = by_id.get(pid)
            if p is None:
                continue
            action = _clip(dec.get("action"), ACTIONS, "rest")
            emotion = _clip(dec.get("emotion"), EMOTIONS, "content")
            intent = _clip(dec.get("future_intent") or dec.get("intent"),
                           INTENTS, "endure")
            memory = str(dec.get("memory", "") or "")[:240]
            dialogue = str(dec.get("dialogue", "") or "")[:240]

            # --- advisory application to the person's inner life ---
            p.last_action = action
            p.emotion = emotion
            p.intent = intent
            p.last_dialogue = dialogue
            p.mind_source = "teacher"
            nudge = _EMOTION_MOOD.get(emotion, 0.0)
            p.mood = max(-1.0, min(1.0, 0.7 * p.mood + 0.3 * nudge))
            if memory:
                p.remember(memory, action, world.tick, valence=nudge)

            # --- one canonical training sample ---
            self.dataset.append(Sample(
                input={
                    "world_state": ws,
                    "citizen_profile": self.batcher.citizen_profile(p),
                    "recent_events": self.batcher.recent_events(p),
                    "relationship_graph": self.batcher.relationship_graph(p),
                    "player_question": None,
                },
                output={"action": action, "emotion": emotion,
                        "memory_update": memory, "dialogue": dialogue,
                        "future_intent": intent},
                meta={
                    "channel": "behavior", "source": "teacher", "model": self.model,
                    "city_id": cohort.city_id, "reason": cohort.reason,
                    "species_id": p.species_id,
                    "features": self.batcher.features(p, city, world),
                    "memory_emb": self.embedder.embed(memory) if memory else None,
                    "dialogue_emb": self.embedder.embed(dialogue) if dialogue else None,
                    "embed_kind": self.embedder.kind,
                },
            ))
            applied += 1
        return applied

    def status(self) -> dict:
        return {"cohorts_run": self.cohorts_run,
                "citizens_taught": self.citizens_taught,
                "last_reason": self.last_reason}
