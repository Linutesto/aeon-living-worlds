"""Per-species neural policies that learn from how their people fare.

Each species gets its own small policy network mapping an individual's state vector
(personality + needs + circumstance, see population.features) to a preference over
life actions. Individuals sample actions biased by their species' policy, so as the
policy learns, the *species* develops characteristic behavior — humans leaning to
trade and expansion, others to migration or aggression — none of it scripted.

Learning is **Advantage-Weighted Regression** (AWR), not vanilla REINFORCE. The actions
these samples record were never drawn from this policy — they come from the blended
utility sampler in `agents/traits.choose_action`, and they sit in a replay buffer — so
the data is inherently off-policy. Plain policy-gradient on off-policy replay is what made
the *first* models drift/oscillate: every step fought stale actions with a single scalar
baseline and no regularizer, so distributions collapsed. AWR is off-policy-correct: it
turns learning into a *weighted supervised* problem — advantages are normalized, turned
into bounded exponential weights, and used to weight the log-likelihood of the action that
was actually taken. Three guards keep behavior stable instead of drifting:

  * **advantage normalization** (zero-mean/unit-std per batch) — no runaway scaling,
  * **entropy regularization** — a floor that stops the policy from collapsing onto one
    action,
  * a **KL trust region** on every update — if a step moves the action distribution more
    than `kl_cap`, the parameter delta is scaled back toward the pre-step weights. The
    realized KL is reported as the visible *drift* metric.

PyTorch (GPU) is used when importable; otherwise an equivalent numpy policy runs so
the simulation never blocks on the ML stack. Both backends implement the *same* AWR math.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from ..agents.spatial import SPATIAL_FEATURES
from ..agents.traits import ACTIONS

log = logging.getLogger("aeon.ai")
LEGACY_N_FEAT = 24
N_FEAT = LEGACY_N_FEAT + len(SPATIAL_FEATURES)
N_ACT = len(ACTIONS)
REPLAY_MAX = 20000          # bounded recency buffer (was 60k — stale samples drove drift)
BATCH_SIZE = 512


def _coerce_features(feats) -> list[float]:
    vals = list(feats or [])
    if len(vals) < N_FEAT:
        vals = vals + [0.0] * (N_FEAT - len(vals))
    elif len(vals) > N_FEAT:
        vals = vals[:N_FEAT]
    return [float(x) for x in vals]


# --- AWR / stability hyperparameters (shared by both backends) -----------------
AWR_TEMP = 1.0              # advantage temperature; higher = softer weighting
AWR_WEIGHT_CLIP = 20.0      # max exp(advantage) weight, guards against outliers
ENTROPY_BETA = 0.01         # entropy bonus; keeps the policy from collapsing
KL_CAP = 0.05               # per-update trust region on the action distribution
TORCH_LR = 1e-3             # was 3e-3 — calmer steps, less oscillation
NUMPY_LR = 0.01             # was 0.02

try:
    import torch
    import torch.nn as nn
    _TORCH = True
    _DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
except Exception:  # noqa: BLE001
    _TORCH = False
    _DEVICE = "cpu"


# ---------------------------------------------------------------- torch backend
if _TORCH:
    class _Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(N_FEAT, 32), nn.Tanh(),
                nn.Linear(32, N_ACT))

        def forward(self, x):
            return self.net(x)


class _TorchPolicy:
    def __init__(self):
        self.net = _Net().to(_DEVICE)
        self.opt = torch.optim.Adam(self.net.parameters(), lr=TORCH_LR)
        self.baseline = 0.0
        self.last_kl = 0.0          # realized drift of the most recent update
        self.last_entropy = 0.0

    def bias(self, feats: list[float]) -> list[float]:
        with torch.no_grad():
            x = torch.tensor(_coerce_features(feats), dtype=torch.float32, device=_DEVICE)
            logits = self.net(x)
            return torch.softmax(logits, -1).mul(N_ACT).cpu().tolist()

    def learn(self, batch: list[dict]) -> float:
        """One AWR update with an entropy bonus and a KL trust region."""
        if len(batch) < 16:
            return 0.0
        feats = torch.tensor([_coerce_features(b["features"]) for b in batch],
                             dtype=torch.float32, device=_DEVICE)
        acts = torch.tensor([ACTIONS.index(b["action"]) for b in batch],
                            dtype=torch.long, device=_DEVICE)
        rewards = torch.tensor([b["reward"] for b in batch],
                               dtype=torch.float32, device=_DEVICE)
        # bounded, normalized advantage → exponential weights (mean 1)
        self.baseline = 0.9 * self.baseline + 0.1 * float(rewards.mean())
        adv = rewards - self.baseline
        adv = (adv - adv.mean()) / (adv.std() + 1e-6)
        w = torch.exp(torch.clamp(adv / AWR_TEMP, max=math.log(AWR_WEIGHT_CLIP)))
        w = w / (w.mean() + 1e-8)

        logits = self.net(feats)
        logp = torch.log_softmax(logits, -1)
        probs = logp.exp()
        old_probs = probs.detach().clone()          # pre-step reference for KL
        chosen = logp.gather(1, acts.unsqueeze(1)).squeeze(1)
        entropy = -(probs * logp).sum(-1).mean()
        # weighted negative log-likelihood of taken actions, minus an entropy floor
        loss = -(w.detach() * chosen).mean() - ENTROPY_BETA * entropy

        prev = [p.detach().clone() for p in self.net.parameters()]
        self.opt.zero_grad(); loss.backward()
        torch.nn.utils.clip_grad_norm_(self.net.parameters(), 5.0)
        self.opt.step()

        # KL trust region: if the distribution moved too far, scale the step back.
        with torch.no_grad():
            new_logp = torch.log_softmax(self.net(feats), -1)
            kl = float((old_probs * (old_probs.clamp_min(1e-8).log()
                                     - new_logp)).sum(-1).mean())
            if kl > KL_CAP and kl > 0:
                scale = math.sqrt(KL_CAP / kl)
                for p, p0 in zip(self.net.parameters(), prev):
                    p.copy_(p0 + scale * (p - p0))
                kl = KL_CAP
        self.last_kl = kl
        self.last_entropy = float(entropy.detach())
        return float(loss.item())

    def state(self) -> dict:
        return {"net": self.net.state_dict(), "opt": self.opt.state_dict(),
                "baseline": self.baseline}

    def load_state(self, state: dict) -> None:
        self.net.load_state_dict(state["net"])
        if "opt" in state:
            self.opt.load_state_dict(state["opt"])
        self.baseline = float(state.get("baseline", 0.0))


# ---------------------------------------------------------------- numpy backend
class _NumpyPolicy:
    def __init__(self):
        self.W = np.random.randn(N_FEAT, N_ACT).astype(np.float32) * 0.1
        self.b = np.zeros(N_ACT, dtype=np.float32)
        self.lr = NUMPY_LR
        self.baseline = 0.0
        self.last_kl = 0.0
        self.last_entropy = 0.0

    def _softmax(self, X):
        Z = X @ self.W + self.b
        P = np.exp(Z - Z.max(1, keepdims=True))
        return P / P.sum(1, keepdims=True)

    def bias(self, feats):
        z = np.asarray(_coerce_features(feats), dtype=np.float32) @ self.W + self.b
        e = np.exp(z - z.max())
        return (e / e.sum() * N_ACT).tolist()

    def learn(self, batch):
        """AWR update — the numpy mirror of `_TorchPolicy.learn` (same math)."""
        if len(batch) < 16:
            return 0.0
        X = np.array([_coerce_features(b["features"]) for b in batch], dtype=np.float32)
        a = np.array([ACTIONS.index(b["action"]) for b in batch])
        r = np.array([b["reward"] for b in batch], dtype=np.float32)
        n = len(batch)
        # bounded, normalized advantage → exponential weights (mean 1)
        self.baseline = 0.9 * self.baseline + 0.1 * float(r.mean())
        adv = r - self.baseline
        adv = (adv - adv.mean()) / (adv.std() + 1e-6)
        w = np.exp(np.clip(adv / AWR_TEMP, None, math.log(AWR_WEIGHT_CLIP)))
        w = w / (w.mean() + 1e-8)

        P = self._softmax(X)
        logP = np.log(P + 1e-12)
        onehot = np.zeros_like(P); onehot[np.arange(n), a] = 1
        # ∂(weighted NLL)/∂logits = w·(P − onehot)
        g_nll = (P - onehot) * w[:, None]
        # ∂(−β·entropy)/∂logits = β·P·(logP + H), H = −Σ P·logP
        H = -(P * logP).sum(1, keepdims=True)
        g_ent = ENTROPY_BETA * P * (logP + H)
        grad = (g_nll + g_ent) / n
        W_prev, b_prev = self.W.copy(), self.b.copy()
        self.W -= self.lr * (X.T @ grad)
        self.b -= self.lr * grad.sum(0)

        # KL trust region (mirror of the torch guard)
        new_P = self._softmax(X)
        kl = float((P * (logP - np.log(new_P + 1e-12))).sum(1).mean())
        if kl > KL_CAP and kl > 0:
            scale = math.sqrt(KL_CAP / kl)
            self.W = W_prev + scale * (self.W - W_prev)
            self.b = b_prev + scale * (self.b - b_prev)
            kl = KL_CAP
        self.last_kl = kl
        self.last_entropy = float(H.mean())
        return float(np.abs(grad).mean())

    def state(self) -> dict:
        return {"W": self.W, "b": self.b, "baseline": self.baseline}

    def load_state(self, state: dict) -> None:
        self.W = state["W"]
        self.b = state["b"]
        self.baseline = float(state.get("baseline", 0.0))


class SpeciesBrain:
    """Holds one learnable policy per species and serves action biases."""

    def __init__(self):
        self.backend = "torch:" + _DEVICE if _TORCH else "numpy"
        self.policies: dict[int, object] = {}
        self.updates = 0
        self.last_loss = 0.0
        self.samples_seen = 0
        self.batches = 0
        self.last_confidence = 0.0
        self.drift = 0.0              # EMA of per-update KL — the headline "drift" number
        self.entropy = 0.0           # EMA of policy entropy — collapse early-warning
        self.behavior_delta: dict[str, float] = {a: 0.0 for a in ACTIONS}
        self.replay: list[dict] = []
        self.samples_collected = 0
        log.info("SpeciesBrain backend: %s", self.backend)

    def _policy(self, species_id: int):
        pol = self.policies.get(species_id)
        if pol is None:
            pol = (_TorchPolicy() if _TORCH else _NumpyPolicy())
            self.policies[species_id] = pol
        return pol

    def action_bias(self, person, city=None, world=None):
        from ..agents.population import PopulationManager
        feats = PopulationManager.features(person, city, world)
        return self._policy(person.species_id).bias(feats)

    def learn(self, experience: list[dict]) -> None:
        """Update every species' policy from the shared experience buffer."""
        self.add_samples(experience)
        experience = self.replay
        if len(experience) < 32:
            return
        by_species: dict[int, list[dict]] = {}
        for e in experience:
            by_species.setdefault(e["species_id"], []).append(e)
        for sid, batch in by_species.items():
            if len(batch) < 16:
                continue
            if len(batch) > BATCH_SIZE:
                idx = np.random.choice(len(batch), BATCH_SIZE, replace=False)
                train = [batch[int(i)] for i in idx]
            else:
                train = batch
            pol = self._policy(sid)
            self.last_loss = pol.learn(train)
            # roll the per-update drift (KL) and entropy into smooth EMAs
            self.drift = 0.9 * self.drift + 0.1 * float(getattr(pol, "last_kl", 0.0))
            self.entropy = 0.9 * self.entropy + 0.1 * float(getattr(pol, "last_entropy", 0.0))
            self.batches += 1
            self._track_confidence(sid, train[-64:])
        self.updates += 1

    def add_samples(self, samples: list[dict]) -> None:
        valid = [s for s in samples
                 if s.get("action") in ACTIONS
                 and len(s.get("features", [])) in (LEGACY_N_FEAT, N_FEAT)]
        if not valid:
            return
        self.replay.extend(valid)
        if len(self.replay) > REPLAY_MAX:
            self.replay = self.replay[-REPLAY_MAX:]
        self.samples_collected += len(valid)
        self.samples_seen = len(self.replay)

    def status(self) -> dict:
        return {"backend": self.backend, "species": len(self.policies),
                "updates": self.updates, "last_loss": round(self.last_loss, 4),
                "samples": self.samples_seen, "samples_collected": self.samples_collected,
                "batches": self.batches,
                "confidence": round(self.last_confidence, 3),
                "drift": round(self.drift, 4),        # mean per-update KL (≤ KL_CAP)
                "entropy": round(self.entropy, 4),    # policy entropy (collapse warning)
                "algo": "awr",
                "behavior_delta": {k: round(v, 3)
                                   for k, v in self.behavior_delta.items()}}

    def _track_confidence(self, sid: int, batch: list[dict]) -> None:
        if not batch:
            return
        pol = self._policy(sid)
        probs = np.array([pol.bias(b["features"]) for b in batch], dtype=np.float32)
        probs = probs / max(1, N_ACT)
        self.last_confidence = float(np.mean(probs.max(axis=1)))
        mean = probs.mean(axis=0)
        uniform = 1.0 / N_ACT
        self.behavior_delta = {a: float(mean[i] - uniform)
                               for i, a in enumerate(ACTIONS)}

    def save_weights(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"backend": self.backend, "updates": self.updates,
                "last_loss": self.last_loss, "samples_seen": self.samples_seen,
                "samples_collected": self.samples_collected,
                "batches": self.batches, "last_confidence": self.last_confidence,
                "drift": self.drift, "entropy": self.entropy,
                "behavior_delta": self.behavior_delta,
                "replay": self.replay[-REPLAY_MAX:],
                "policies": {sid: pol.state()
                             for sid, pol in self.policies.items()
                             if hasattr(pol, "state")}}
        if _TORCH:
            torch.save(data, path)
        else:
            import pickle
            path.write_bytes(pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))

    def load_weights(self, path: str | Path | None) -> None:
        if path is None or not Path(path).exists():
            return
        if _TORCH:
            # weights_only=False: this is our own trusted checkpoint and it carries
            # plain python/numpy objects (replay buffer, metadata), not just tensors.
            # PyTorch 2.6 flipped the default to True, which rejects numpy scalars.
            data = torch.load(path, map_location=_DEVICE, weights_only=False)
        else:
            import pickle
            data = pickle.loads(Path(path).read_bytes())
        self.updates = int(data.get("updates", 0))
        self.last_loss = float(data.get("last_loss", 0.0))
        self.samples_seen = int(data.get("samples_seen", 0))
        self.samples_collected = int(data.get("samples_collected", self.samples_seen))
        self.batches = int(data.get("batches", 0))
        self.last_confidence = float(data.get("last_confidence", 0.0))
        self.drift = float(data.get("drift", 0.0))
        self.entropy = float(data.get("entropy", 0.0))
        self.behavior_delta = data.get("behavior_delta", self.behavior_delta)
        self.replay = data.get("replay", [])[-REPLAY_MAX:]
        for sid, state in data.get("policies", {}).items():
            try:
                self._policy(int(sid)).load_state(state)
            except Exception:  # noqa: BLE001
                log.warning("skipped incompatible saved policy for species %s", sid)
