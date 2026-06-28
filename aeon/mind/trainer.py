"""SocietyTrainer — the live distillation loop that makes the GPU sweat.

Each step samples a behavior-channel minibatch from the corpus, encodes it, runs the
**training** copy of the CfC net, and minimizes a weighted sum of three cross-entropies
(action / emotion / intent) plus two cosine-embedding losses (memory / dialogue). It
tracks loss, per-head accuracy, and **teacher-agreement** (how often the student's top
action matches the teacher label) — the signal the hybrid runtime uses to decide how
much of the population the student is trusted to drive. Weights are published to the
serving net every `swap_every` steps.

`train_step()` is synchronous and meant to be called from a worker thread
(`asyncio.to_thread`) so backprop never blocks the sim's event loop; the serving net it
publishes to is read under a lock, so inference in the loop stays safe.
"""

from __future__ import annotations

import logging
import random
from collections import Counter

import torch
import torch.nn.functional as F

from . import encode as enc
from .dataset import SocietyDataset
from .liquid import DoubleBufferedNet

log = logging.getLogger("aeon.mind.trainer")


class SocietyTrainer:
    def __init__(self, net: DoubleBufferedNet, dataset: SocietyDataset, *,
                 lr: float = 2e-3, batch_size: int = 256, swap_every: int = 5,
                 min_samples: int = 64, val_fraction: float = 0.12,
                 val_every: int = 25, awr_beta: float = 1.0,
                 balance_actions: bool = False, min_for_split: int = 256) -> None:
        self.net = net
        self.dataset = dataset
        self.batch_size = batch_size
        self.swap_every = swap_every
        self.min_samples = min_samples
        self.val_fraction = float(val_fraction)
        self.val_every = max(1, int(val_every))
        self.awr_beta = float(awr_beta)         # advantage-weighted regression temperature
        self.balance_actions = bool(balance_actions)
        # only hold out a validation split once the corpus is big enough to afford it;
        # below this, train on everything (a tiny cold-start corpus can't spare 12%).
        self.min_for_split = int(min_for_split)
        self.opt = torch.optim.Adam(net.training_net.parameters(), lr=lr)
        self.rng = random.Random(0)
        self.val_rng = random.Random(1)
        # live metrics (read by serialize_mind)
        self.steps = 0
        self.samples_trained = 0
        self.last_loss = 0.0
        self.ema_loss = 0.0
        self.action_acc = 0.0
        self.emotion_acc = 0.0
        self.intent_acc = 0.0
        self.target_acc = 0.0
        self.agreement = 0.0          # EMA of student↔teacher top-action match
        self.teacher_sampling_ratio = 0.95
        self.teacher_override_rate = 1.0
        self.disagreement_hotspots: dict[str, dict[str, int]] = {
            "action": {}, "emotion": {}, "intent": {}, "target": {},
        }
        self.drift_score = 1.0
        self.capability_score = 0.0
        self.loss_curve: list[float] = []
        self.ready = False
        # held-out validation metrics + drift detection
        self.val_action_acc = 0.0
        self.val_emotion_acc = 0.0
        self.val_intent_acc = 0.0
        self.val_target_acc = 0.0
        self.val_capability = 0.0
        self.best_val_capability = 0.0
        self.val_drift = 0.0
        self.validations = 0

    def _trainable(self) -> bool:
        return self.dataset.channel_size("behavior") >= self.min_samples

    def train_step(self) -> dict:
        """One optimizer step. Returns metrics (empty dict if not enough data yet)."""
        if not self._trainable():
            return {}
        # Draw the training minibatch from the TRAIN split (val held out) once the corpus
        # is large enough, using prioritized teacher/disagreement replay — or
        # class-balanced replay if enabled.
        split = "train" if self.dataset.channel_size("behavior") >= self.min_for_split else None
        if self.balance_actions:
            batch = self.dataset.sample_batch(
                self.batch_size, channel="behavior", rng=self.rng,
                split=split, val_fraction=self.val_fraction, balance_key="action")
        else:
            batch = self.dataset.sample_batch(
                self.batch_size, channel="behavior", rng=self.rng,
                teacher_ratio=self.teacher_sampling_ratio,
                prioritize_disagreement=True,
                split=split, val_fraction=self.val_fraction)
        if len(batch) < self.min_samples:
            return {}
        dev = self.net.device
        t = enc.encode_batch(batch, device=dev)
        net = self.net.training_net
        net.train()
        heads, _ = net(t["x_seq"], t["dt"])

        # Advantage-weighted regression on the action head: samples the teacher flagged
        # as high-priority (corrections, disagreements, explicit advantage) get more
        # gradient. With no advantage signal every weight is 1.0 → plain behavior cloning.
        adv_w = self._awr_weights(batch, dev)
        loss_a = (F.cross_entropy(heads["action"], t["y_action"], reduction="none")
                  * adv_w).sum() / adv_w.sum()
        loss_e = F.cross_entropy(heads["emotion"], t["y_emotion"])
        loss_i = F.cross_entropy(heads["intent"], t["y_intent"])
        loss_t = F.cross_entropy(heads["target"], t["y_target"])
        # cosine-embedding loss only where a text target exists
        mask = t["has_text"]
        target = torch.ones(len(batch), device=dev)
        loss_m = (F.cosine_embedding_loss(heads["memory"], t["memory_emb"], target,
                                          reduction="none") * mask).sum() / mask.clamp(min=1).sum()
        loss_d = (F.cosine_embedding_loss(heads["dialogue"], t["dialogue_emb"], target,
                                          reduction="none") * mask).sum() / mask.clamp(min=1).sum()
        loss = 1.35 * loss_a + 0.5 * loss_e + 0.5 * loss_i + 0.35 * loss_t \
            + 0.3 * loss_m + 0.3 * loss_d

        self.opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
        self.opt.step()

        with torch.no_grad():
            pred_a = heads["action"].argmax(-1)
            acc_a = float((pred_a == t["y_action"]).float().mean())
            acc_e = float((heads["emotion"].argmax(-1) == t["y_emotion"]).float().mean())
            acc_i = float((heads["intent"].argmax(-1) == t["y_intent"]).float().mean())
            pred_t = heads["target"].argmax(-1)
            acc_t = float((pred_t == t["y_target"]).float().mean())

        self.steps += 1
        self.samples_trained += len(batch)
        self.last_loss = float(loss.item())
        a = 0.05
        self.ema_loss = self.last_loss if self.steps == 1 else \
            (1 - a) * self.ema_loss + a * self.last_loss
        self.action_acc = (1 - a) * self.action_acc + a * acc_a
        self.emotion_acc = (1 - a) * self.emotion_acc + a * acc_e
        self.intent_acc = (1 - a) * self.intent_acc + a * acc_i
        self.target_acc = (1 - a) * self.target_acc + a * acc_t
        combined = (acc_a + acc_e + acc_i + acc_t) / 4.0
        self.agreement = (1 - a) * self.agreement + a * combined
        self.teacher_sampling_ratio = self._teacher_ratio()
        self.teacher_override_rate = max(0.0, min(1.0, self.teacher_sampling_ratio))
        self.drift_score = round(max(0.0, 1.0 - self.agreement), 4)
        self.capability_score = round(
            max(0.0, min(1.0, 0.35 * self.action_acc + 0.2 * self.emotion_acc
                         + 0.2 * self.intent_acc + 0.25 * self.target_acc)), 4)
        self._track_disagreements(batch, heads, t)
        self.loss_curve.append(round(self.last_loss, 4))
        self.loss_curve = self.loss_curve[-240:]
        self.ready = True

        if self.steps % self.val_every == 0:
            self.validate()

        if self.steps % self.swap_every == 0:
            self.net.swap()
        return {"loss": self.last_loss, "action_acc": acc_a,
                "target_acc": acc_t, "step": self.steps}

    def _awr_weights(self, batch: list[dict], dev) -> torch.Tensor:
        """Per-sample advantage → bounded weight (advantage-weighted regression).

        Advantage is a real *return* signal (`meta.advantage` or `meta.reward`), centered
        across the batch so above-average outcomes are reinforced and below-average ones
        damped — never to zero. When the batch carries no return signal (pure teacher
        behavior labels) every weight is 1.0 and this is plain behavior cloning, so
        distillation is never destabilized by upweighting a biased subset."""
        raw = []
        for r in batch:
            meta = r.get("meta", {})
            a = meta.get("advantage", meta.get("reward"))
            raw.append(float(a) if a is not None else 0.0)
        adv = torch.tensor(raw, dtype=torch.float32, device=dev)
        if float(adv.max() - adv.min()) < 1e-6:        # no return signal → pure BC
            return torch.ones(len(batch), device=dev)
        adv = (adv - adv.mean()) / (adv.std() + 1e-6)
        return torch.exp(self.awr_beta * adv).clamp(0.25, 4.0)

    @torch.no_grad()
    def validate(self) -> dict:
        """Measure accuracy on the held-out val split — data the optimizer never sees —
        and update drift (a regression of capability below its historical best). No-ops
        until the corpus is big enough to hold out a split."""
        if self.dataset.channel_size("behavior") < self.min_for_split:
            return {}
        batch = self.dataset.sample_batch(
            self.batch_size, channel="behavior", rng=self.val_rng,
            split="val", val_fraction=self.val_fraction)
        if len(batch) < 8:
            return {}
        t = enc.encode_batch(batch, device=self.net.device)
        net = self.net.training_net
        net.eval()
        heads, _ = net(t["x_seq"], t["dt"])
        net.train()
        self.val_action_acc = float((heads["action"].argmax(-1) == t["y_action"]).float().mean())
        self.val_emotion_acc = float((heads["emotion"].argmax(-1) == t["y_emotion"]).float().mean())
        self.val_intent_acc = float((heads["intent"].argmax(-1) == t["y_intent"]).float().mean())
        self.val_target_acc = float((heads["target"].argmax(-1) == t["y_target"]).float().mean())
        self.val_capability = round(0.35 * self.val_action_acc + 0.2 * self.val_emotion_acc
                                    + 0.2 * self.val_intent_acc + 0.25 * self.val_target_acc, 4)
        self.best_val_capability = max(self.best_val_capability, self.val_capability)
        # drift = how far validation capability has regressed from its best (overfitting
        # / catastrophic forgetting shows up here even while train accuracy looks fine).
        self.val_drift = round(max(0.0, self.best_val_capability - self.val_capability), 4)
        self.validations += 1
        return {"val_capability": self.val_capability, "val_drift": self.val_drift}

    def _teacher_ratio(self) -> float:
        if not self.ready or self.steps < 40:
            return 0.98
        floor = min(self.action_acc, self.emotion_acc, self.intent_acc, self.target_acc)
        if self.agreement < 0.62 or floor < 0.55:
            return 0.95
        if self.agreement < 0.74 or floor < 0.66:
            return 0.70
        if self.agreement < 0.84 or floor < 0.78:
            return 0.50
        return 0.35

    def _track_disagreements(self, batch: list[dict], heads: dict, t: dict) -> None:
        preds = {
            "action": heads["action"].argmax(-1).detach().cpu().tolist(),
            "emotion": heads["emotion"].argmax(-1).detach().cpu().tolist(),
            "intent": heads["intent"].argmax(-1).detach().cpu().tolist(),
            "target": heads["target"].argmax(-1).detach().cpu().tolist(),
        }
        labels = {
            "action": t["y_action"].detach().cpu().tolist(),
            "emotion": t["y_emotion"].detach().cpu().tolist(),
            "intent": t["y_intent"].detach().cpu().tolist(),
            "target": t["y_target"].detach().cpu().tolist(),
        }
        vocabs = {"action": enc.ACTIONS, "emotion": enc.EMOTIONS,
                  "intent": enc.INTENTS, "target": enc.TARGET_KINDS}
        for head in preds:
            c = Counter(self.disagreement_hotspots.get(head, {}))
            for i, pred in enumerate(preds[head]):
                if pred == labels[head][i]:
                    continue
                label = vocabs[head][labels[head][i]]
                c[label] += 1
                if self.steps % 5 == 0 and i < 6:
                    rec = dict(batch[i])
                    meta = dict(rec.get("meta", {}))
                    meta.update({"source": "teacher_correction",
                                 "priority": 1.0,
                                 "disagreement": {"head": head, "label": label}})
                    rec["meta"] = meta
                    self.dataset.append(rec)
            self.disagreement_hotspots[head] = dict(c.most_common(8))

    def status(self) -> dict:
        gpu_mb = 0.0
        if torch.cuda.is_available():
            gpu_mb = round(torch.cuda.memory_allocated() / 1e6, 1)
        return {
            "backend": self.net.device,
            "params": self.net.training_net.n_params(),
            "steps": self.steps,
            "samples_trained": self.samples_trained,
            "last_loss": round(self.last_loss, 4),
            "ema_loss": round(self.ema_loss, 4),
            "action_acc": round(self.action_acc, 3),
            "emotion_acc": round(self.emotion_acc, 3),
            "intent_acc": round(self.intent_acc, 3),
            "target_acc": round(self.target_acc, 3),
            "agreement": round(self.agreement, 3),
            "teacher_sampling_ratio": round(self.teacher_sampling_ratio, 3),
            "teacher_override_rate": round(self.teacher_override_rate, 3),
            "student_autonomy": round(max(0.0, 1.0 - self.teacher_override_rate), 3),
            "disagreement_hotspots": self.disagreement_hotspots,
            # prefer the held-out validation drift once we have measured it; fall back to
            # the train agreement-drift during the cold-start window.
            "regression_drift_score": self.val_drift if self.validations else self.drift_score,
            "agreement_drift_score": self.drift_score,
            "capability_score": self.capability_score,
            "val_action_acc": round(self.val_action_acc, 3),
            "val_emotion_acc": round(self.val_emotion_acc, 3),
            "val_intent_acc": round(self.val_intent_acc, 3),
            "val_target_acc": round(self.val_target_acc, 3),
            "val_capability": round(self.val_capability, 4),
            "best_val_capability": round(self.best_val_capability, 4),
            "val_drift": self.val_drift,
            "validations": self.validations,
            "loss_curve": self.loss_curve[-120:],
            "gpu_mb": gpu_mb,
            "version": self.net.version,
            "ready": self.ready,
        }
