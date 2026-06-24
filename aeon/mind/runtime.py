"""HybridMind — the routing brain that ties teacher, dataset, student, and sim together.

Owns the corpus, the double-buffered CfC student, the trainer, the cohort batcher and
(optionally) the teacher. Two jobs at runtime:

  • decide_batch(persons): for the per-tick life loop, route each eligible person to the
    cheap student or leave them on the utility model. As the student's agreement with the
    teacher rises, its **share** of the population grows — so you literally watch the model
    take over. One batched GPU forward serves the whole cohort.
  • train_step(): one background distillation step (called via asyncio.to_thread).

Crises and notable figures are steered by the teacher cohort loop (teacher.py); this
class handles the bulk, routine cognition.
"""

from __future__ import annotations

import logging

from .cohort import CohortBatcher, world_state
from .dataset import SocietyDataset
from .encode import ACTIONS, EMOTIONS, INTENTS, encode_batch
from .liquid import DoubleBufferedNet, DEVICE
from .trainer import SocietyTrainer

log = logging.getLogger("aeon.mind.runtime")


class HybridMind:
    def __init__(self, cfg=None, *, dataset_dir, society=None) -> None:
        c = cfg or _Defaults()
        self.dataset = SocietyDataset(dataset_dir)
        self.batcher = CohortBatcher(min_size=getattr(c, "cohort_min", 6),
                                     max_size=getattr(c, "cohort_size", 300))
        self.net = DoubleBufferedNet(hidden=getattr(c, "hidden", 128),
                                     layers=getattr(c, "layers", 2), device=DEVICE)
        self.trainer = SocietyTrainer(
            self.net, self.dataset, lr=getattr(c, "lr", 2e-3),
            batch_size=getattr(c, "batch_size", 256),
            swap_every=getattr(c, "swap_every", 5),
            min_samples=getattr(c, "min_samples", 64))
        self.confidence_gate = float(getattr(c, "confidence_gate", 0.4))
        self.warmup_steps = int(getattr(c, "warmup_steps", 20))
        self.tier_counts = {"student": 0, "teacher": 0, "utility": 0}
        self._society = society

    # --------------------------------------------------------------- routing
    @property
    def agreement(self) -> float:
        return self.trainer.agreement

    def student_share(self) -> float:
        """Fraction of the population the student is trusted to drive (0..1)."""
        if not self.trainer.ready or self.trainer.steps < self.warmup_steps:
            return 0.0
        if self.confidence_gate <= 0:
            return 1.0
        return max(0.0, min(1.0, self.agreement / self.confidence_gate))

    def _student_driven(self, pid: int, share: float) -> bool:
        # deterministic per-person threshold so the takeover is stable, not flickery
        return (hash(("mind", pid)) % 1000) / 1000.0 < share

    def build_record(self, p, city, world) -> dict:
        return {"input": {
            "world_state": world_state(world, self._society),
            "recent_events": self.batcher.recent_events(p),
            "relationship_graph": self.batcher.relationship_graph(p),
        }, "output": {},
            "meta": {"features": self.batcher.features(p, city, world)}}

    def decide_batch(self, persons: list, world) -> dict:
        """Return {pid: {action, emotion, intent}} for student-routed persons."""
        share = self.student_share()
        chosen = [p for p in persons if self._student_driven(p.id, share)]
        self.tier_counts = {"student": len(chosen), "teacher": 0,
                            "utility": len(persons) - len(chosen)}
        if not chosen:
            return {}
        records = [self.build_record(
            p, world.cities.get(p.home_city) if p.home_city else None, world)
            for p in chosen]
        t = encode_batch(records, device=self.net.device)
        heads = self.net.infer(t["x_seq"], t["dt"])
        a = heads["action"].argmax(-1).tolist()
        e = heads["emotion"].argmax(-1).tolist()
        i = heads["intent"].argmax(-1).tolist()
        decisions = {}
        for k, p in enumerate(chosen):
            decisions[p.id] = {"action": ACTIONS[a[k]], "emotion": EMOTIONS[e[k]],
                               "intent": INTENTS[i[k]]}
        return decisions

    # --------------------------------------------------------------- training
    def train_step(self) -> dict:
        return self.trainer.train_step()

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        st = self.trainer.status()
        st.update({
            "dataset": self.dataset.stats(),
            "student_share": round(self.student_share(), 3),
            "confidence_gate": self.confidence_gate,
            "tier_counts": dict(self.tier_counts),
        })
        return st

    def save(self, path) -> None:
        self.net.save(path)

    def load(self, path) -> bool:
        return self.net.load(path)


class _Defaults:
    """Used when no MindConfig is supplied (tests, ad-hoc construction)."""
    pass
