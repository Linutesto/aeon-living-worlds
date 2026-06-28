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
import threading

from .cohort import CohortBatcher, world_state
from .curriculum import TeacherCurriculum
from .dataset import SocietyDataset
from .encode import ACTIONS, EMOTIONS, INTENTS, TARGET_KINDS, encode_batch
from .liquid import (DoubleBufferedNet, DEVICE, MODEL_SIZES, clamp_dims,
                     model_options, resolve_dims)
from .trainer import SocietyTrainer

log = logging.getLogger("aeon.mind.runtime")


class HybridMind:
    def __init__(self, cfg=None, *, dataset_dir, society=None) -> None:
        c = cfg or _Defaults()
        self.dataset = SocietyDataset(dataset_dir)
        self.batcher = CohortBatcher(min_size=getattr(c, "cohort_min", 6),
                                     max_size=getattr(c, "cohort_size", 300))
        # the trainer kwargs are stashed so the student can be rebuilt at a new size
        # live (from the dashboard) without re-reading config — see rebuild_student().
        self._trainer_kwargs = dict(
            lr=getattr(c, "lr", 2e-3),
            batch_size=getattr(c, "batch_size", 256),
            swap_every=getattr(c, "swap_every", 5),
            min_samples=getattr(c, "min_samples", 64),
            val_fraction=getattr(c, "val_fraction", 0.12),
            val_every=getattr(c, "val_every", 25),
            awr_beta=getattr(c, "awr_beta", 1.0),
            balance_actions=getattr(c, "balance_actions", False),
            min_for_split=getattr(c, "min_for_split", 256))
        # held while a train step runs OR the net is rebuilt, so a dashboard-driven
        # resize never tears a backward pass running in the train worker thread.
        self._train_lock = threading.Lock()
        self.net = DoubleBufferedNet(size=getattr(c, "student_size", "tiny"),
                                     hidden=getattr(c, "hidden", None),
                                     layers=getattr(c, "layers", None), device=DEVICE)
        self.student_size = self.net.size
        self.trainer = SocietyTrainer(self.net, self.dataset, **self._trainer_kwargs)
        self.confidence_gate = float(getattr(c, "confidence_gate", 0.4))
        self.warmup_steps = int(getattr(c, "warmup_steps", 20))
        self.min_teacher_agreement = float(getattr(c, "min_teacher_agreement", 0.74))
        self.population_takeover_threshold = float(
            getattr(c, "population_takeover_threshold", 0.82))
        # Tiered cognition: a bounded set of embodied citizens run the student per tick.
        self.active_embodied_citizens = int(getattr(c, "active_embodied_citizens", 240))
        self.autonomy_ratio = max(0.0, min(1.0, float(getattr(c, "autonomy_ratio", 0.7))))
        # The teacher-priority curriculum gates how much the student is allowed to drive.
        self.curriculum = TeacherCurriculum(
            warmup_steps=self.warmup_steps, autonomy_ratio=self.autonomy_ratio)
        self.tier_counts = {"student": 0, "teacher": 0, "utility": 0}
        self._society = society

    # --------------------------------------------------------------- routing
    @property
    def agreement(self) -> float:
        return self.trainer.agreement

    def student_share(self) -> float:
        """Fraction of the population the student is trusted to drive (0..1).

        Delegated to the teacher-priority curriculum, which gates promotion on ALL of
        action/emotion/intent/target accuracy + capability + drift and rolls back on
        regression. The student never drives anyone in phase_1_teacher_first."""
        self.curriculum.update(self.trainer.status())
        return self.curriculum.student_share()

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
        # Tiered cognition: cap how many embodied citizens the (possibly large) student
        # drives per tick so one GPU forward stays cheap. The overflow stays on the
        # utility model. Deterministic order (by id) keeps the selection stable.
        if len(chosen) > self.active_embodied_citizens:
            chosen = sorted(chosen, key=lambda p: p.id)[:self.active_embodied_citizens]
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
        tk = heads["target"].argmax(-1).tolist()
        decisions = {}
        for k, p in enumerate(chosen):
            decisions[p.id] = {"action": ACTIONS[a[k]], "emotion": EMOTIONS[e[k]],
                               "intent": INTENTS[i[k]],
                               "target_kind": TARGET_KINDS[tk[k]]}
        return decisions

    # --------------------------------------------------------------- training
    def train_step(self) -> dict:
        # the lock is held only for the optimizer step itself; a concurrent rebuild
        # waits for the in-flight backward pass to finish before swapping the net.
        with self._train_lock:
            return self.trainer.train_step()

    # ----------------------------------------------------------- model resize
    def rebuild_student(self, *, size: str | None = None, hidden: int | None = None,
                        layers: int | None = None) -> dict:
        """Swap in a fresh student net at a new size — live, from the dashboard.

        A bigger/smaller liquid net can't be resized in place (the parameter shapes
        change), so we construct a new `DoubleBufferedNet` + a fresh trainer bound to
        it and atomically replace both under `_train_lock`. The corpus (and the teacher)
        are untouched, so the new net starts retraining from the existing data within a
        few steps. The previously trained weights are discarded — this is the one knob
        that resets learning, which the UI warns about."""
        if size is not None:
            size = str(size).lower()
            if size not in MODEL_SIZES:
                return {"ok": False, "message": f"size must be one of {list(MODEL_SIZES)}"}
        hidden, layers = clamp_dims(hidden, layers)
        new_h, new_l = resolve_dims(size=size, hidden=hidden, layers=layers)
        with self._train_lock:
            if new_h == self.net.hidden and new_l == self.net.layers \
                    and (size is None or size == self.net.size):
                return {"ok": False, "message": "student already at that size",
                        "student_size": self.student_size,
                        "hidden": self.net.hidden, "layers": self.net.layers}
            net = DoubleBufferedNet(size=size, hidden=hidden, layers=layers, device=DEVICE)
            self.net = net
            self.student_size = net.size
            # a fresh trainer resets metrics/curriculum gating to match the new net
            self.trainer = SocietyTrainer(net, self.dataset, **self._trainer_kwargs)
            self.curriculum = TeacherCurriculum(
                warmup_steps=self.warmup_steps, autonomy_ratio=self.autonomy_ratio)
        log.info("society student rebuilt: size=%s hidden=%d layers=%d params=%d",
                 net.size, net.hidden, net.layers, net.training_net.n_params())
        return {"ok": True, "student_size": net.size, "hidden": net.hidden,
                "layers": net.layers, "params": net.training_net.n_params(),
                "backend": net.device}

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        st = self.trainer.status()
        share = self.student_share()           # also refreshes the curriculum
        st.update({
            "dataset": self.dataset.stats(),
            "student_share": round(share, 3),
            "student_size": self.student_size,
            "hidden": self.net.hidden,
            "layers": self.net.layers,
            "model_options": model_options(),
            "active_embodied_citizens": self.active_embodied_citizens,
            "autonomy_ratio": self.autonomy_ratio,
            "confidence_gate": self.confidence_gate,
            "min_teacher_agreement": self.min_teacher_agreement,
            "population_takeover_threshold": self.population_takeover_threshold,
            "tier_counts": dict(self.tier_counts),
            "curriculum": self.curriculum.status(),
            "curriculum_phase": self.curriculum_phase(),
            "rollbacks": self.curriculum.rollbacks,
        })
        return st

    def curriculum_phase(self) -> str:
        return self.curriculum.phase

    def save(self, path) -> None:
        self.net.save(path)

    def load(self, path) -> bool:
        return self.net.load(path)


class _Defaults:
    """Used when no MindConfig is supplied (tests, ad-hoc construction)."""
    pass
