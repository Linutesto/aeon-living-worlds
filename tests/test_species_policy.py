"""Tests for the per-species AWR policies (aeon/ai/species_policy.py).

These guard the *behavior-drift* rework: the policies must (1) actually learn from a
reward signal, (2) never let a single update move the action distribution past the KL
trust region, and (3) never collapse to a near-deterministic policy (entropy floor).
The numpy backend is always exercised; the torch backend is exercised when importable.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from aeon.agents.traits import ACTIONS
from aeon.ai import species_policy as sp
from aeon.ai.species_policy import (
    KL_CAP, N_ACT, N_FEAT, SpeciesBrain, _NumpyPolicy,
)


def _batch(target_action: str, n: int = 256, seed: int = 0, species_id: int = 1):
    """Synthetic experience where `target_action` is the high-reward choice."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        act = ACTIONS[int(rng.integers(0, N_ACT))]
        reward = 1.0 if act == target_action else 0.0
        out.append({"species_id": species_id, "action": act, "reward": reward,
                    "features": rng.standard_normal(N_FEAT).astype(np.float32).tolist()})
    return out


def _entropy(probs) -> float:
    p = np.asarray(probs, dtype=np.float64) / N_ACT   # bias() returns softmax*N_ACT
    p = np.clip(p, 1e-12, 1.0)
    return float(-(p * np.log(p)).sum())


# --------------------------------------------------------------- numpy backend
def test_numpy_learns_reward_signal():
    pol = _NumpyPolicy()
    target = "study"
    ti = ACTIONS.index(target)
    start = pol.bias([0.1] * N_FEAT)[ti]
    for k in range(120):
        pol.learn(_batch(target, seed=k))
    end = pol.bias([0.1] * N_FEAT)[ti]
    assert end > start                      # the rewarded action became more likely
    assert math.isfinite(end)


def test_numpy_kl_never_exceeds_cap():
    pol = _NumpyPolicy()
    for k in range(80):
        pol.learn(_batch("feud", seed=k))
        assert pol.last_kl <= KL_CAP + 1e-5     # trust region holds every step


def test_numpy_does_not_collapse():
    pol = _NumpyPolicy()
    for k in range(300):                         # heavy training on one action
        pol.learn(_batch("work", seed=k))
    ent = _entropy(pol.bias([0.0] * N_FEAT))
    # uniform entropy is ln(9)≈2.2; the entropy floor must keep us well off 0.
    assert ent > 0.7, f"policy collapsed (entropy={ent:.3f})"


def test_numpy_weights_finite():
    pol = _NumpyPolicy()
    for k in range(50):
        pol.learn(_batch("court", seed=k))
    assert np.isfinite(pol.W).all() and np.isfinite(pol.b).all()


# ------------------------------------------------------- SpeciesBrain surface
def test_brain_status_reports_drift_and_entropy():
    brain = SpeciesBrain()
    for k in range(40):
        brain.learn(_batch("migrate", seed=k, species_id=3))
    st = brain.status()
    assert st["algo"] == "awr"
    assert 0.0 <= st["drift"] <= KL_CAP + 1e-3      # drift is bounded by the trust region
    assert st["entropy"] > 0.3                       # not collapsed
    # the rewarded action should carry a positive behavior delta
    assert st["behavior_delta"]["migrate"] > min(st["behavior_delta"].values())


def test_brain_save_load_roundtrip(tmp_path):
    brain = SpeciesBrain()
    for k in range(30):
        brain.learn(_batch("worship", seed=k, species_id=7))
    path = tmp_path / "weights.pt"
    brain.save_weights(path)
    fresh = SpeciesBrain()
    fresh.load_weights(path)
    assert fresh.drift == pytest.approx(brain.drift, abs=1e-6)
    assert fresh.entropy == pytest.approx(brain.entropy, abs=1e-6)
    # the loaded policy should rank 'worship' the same as the saved one
    ti = ACTIONS.index("worship")
    feats = [0.2] * N_FEAT
    assert fresh._policy(7).bias(feats)[ti] == pytest.approx(
        brain._policy(7).bias(feats)[ti], abs=1e-4)


# ------------------------------------------------------------- torch backend
def test_torch_backend_learns_and_respects_kl():
    torch = pytest.importorskip("torch")
    pol = sp._TorchPolicy()
    target = "venture"
    ti = ACTIONS.index(target)
    start = pol.bias([0.1] * N_FEAT)[ti]
    for k in range(120):
        pol.learn(_batch(target, seed=k))
        assert pol.last_kl <= KL_CAP + 1e-4
    end = pol.bias([0.1] * N_FEAT)[ti]
    assert end > start
    ent = _entropy(pol.bias([0.0] * N_FEAT))
    assert ent > 0.7, f"torch policy collapsed (entropy={ent:.3f})"
