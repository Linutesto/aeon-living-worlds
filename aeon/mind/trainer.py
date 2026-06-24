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

import torch
import torch.nn.functional as F

from . import encode as enc
from .dataset import SocietyDataset
from .liquid import DoubleBufferedNet

log = logging.getLogger("aeon.mind.trainer")


class SocietyTrainer:
    def __init__(self, net: DoubleBufferedNet, dataset: SocietyDataset, *,
                 lr: float = 2e-3, batch_size: int = 256, swap_every: int = 5,
                 min_samples: int = 64) -> None:
        self.net = net
        self.dataset = dataset
        self.batch_size = batch_size
        self.swap_every = swap_every
        self.min_samples = min_samples
        self.opt = torch.optim.Adam(net.training_net.parameters(), lr=lr)
        self.rng = random.Random(0)
        # live metrics (read by serialize_mind)
        self.steps = 0
        self.samples_trained = 0
        self.last_loss = 0.0
        self.ema_loss = 0.0
        self.action_acc = 0.0
        self.emotion_acc = 0.0
        self.intent_acc = 0.0
        self.agreement = 0.0          # EMA of student↔teacher top-action match
        self.loss_curve: list[float] = []
        self.ready = False

    def _trainable(self) -> bool:
        return self.dataset.channel_size("behavior") >= self.min_samples

    def train_step(self) -> dict:
        """One optimizer step. Returns metrics (empty dict if not enough data yet)."""
        if not self._trainable():
            return {}
        batch = self.dataset.sample_batch(self.batch_size, channel="behavior",
                                          rng=self.rng)
        if len(batch) < self.min_samples:
            return {}
        dev = self.net.device
        t = enc.encode_batch(batch, device=dev)
        net = self.net.training_net
        net.train()
        heads, _ = net(t["x_seq"], t["dt"])

        loss_a = F.cross_entropy(heads["action"], t["y_action"])
        loss_e = F.cross_entropy(heads["emotion"], t["y_emotion"])
        loss_i = F.cross_entropy(heads["intent"], t["y_intent"])
        # cosine-embedding loss only where a text target exists
        mask = t["has_text"]
        target = torch.ones(len(batch), device=dev)
        loss_m = (F.cosine_embedding_loss(heads["memory"], t["memory_emb"], target,
                                          reduction="none") * mask).sum() / mask.clamp(min=1).sum()
        loss_d = (F.cosine_embedding_loss(heads["dialogue"], t["dialogue_emb"], target,
                                          reduction="none") * mask).sum() / mask.clamp(min=1).sum()
        loss = loss_a + 0.5 * loss_e + 0.5 * loss_i + 0.3 * loss_m + 0.3 * loss_d

        self.opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
        self.opt.step()

        with torch.no_grad():
            pred_a = heads["action"].argmax(-1)
            acc_a = float((pred_a == t["y_action"]).float().mean())
            acc_e = float((heads["emotion"].argmax(-1) == t["y_emotion"]).float().mean())
            acc_i = float((heads["intent"].argmax(-1) == t["y_intent"]).float().mean())

        self.steps += 1
        self.samples_trained += len(batch)
        self.last_loss = float(loss.item())
        a = 0.05
        self.ema_loss = self.last_loss if self.steps == 1 else \
            (1 - a) * self.ema_loss + a * self.last_loss
        self.action_acc = (1 - a) * self.action_acc + a * acc_a
        self.emotion_acc = (1 - a) * self.emotion_acc + a * acc_e
        self.intent_acc = (1 - a) * self.intent_acc + a * acc_i
        self.agreement = (1 - a) * self.agreement + a * acc_a   # action-match proxy
        self.loss_curve.append(round(self.last_loss, 4))
        self.loss_curve = self.loss_curve[-240:]
        self.ready = True

        if self.steps % self.swap_every == 0:
            self.net.swap()
        return {"loss": self.last_loss, "action_acc": acc_a, "step": self.steps}

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
            "agreement": round(self.agreement, 3),
            "loss_curve": self.loss_curve[-120:],
            "gpu_mb": gpu_mb,
            "version": self.net.version,
            "ready": self.ready,
        }
