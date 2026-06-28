"""Tests for the PyTorch teacher→student training pipeline upgrades (focus area 3).

Covers advantage-weighted regression (identity without a return signal, real weighting
with one), the held-out validation split + drift detection that only engage on a corpus
large enough to spare a split, class-balanced replay sampling, and a training/checkpoint
smoke test. The numpy fallback for the species policies is checked in test_numpy_fallback.
"""

from __future__ import annotations

import pytest

from aeon.mind import encode as enc
from aeon.mind.dataset import Sample, SocietyDataset

torch = pytest.importorskip("torch")


def _sample(action="work", feat=0.5, reward=None, advantage=None):
    meta = {"channel": "behavior", "source": "teacher", "features": [feat] * enc.N_FEAT}
    if reward is not None:
        meta["reward"] = reward
    if advantage is not None:
        meta["advantage"] = advantage
    return Sample(input={"world_state": {"year": 1}},
                  output={"action": action, "emotion": "anxious",
                          "future_intent": "provide"}, meta=meta)


def _trainer(tmp_path, n=64, **kw):
    from aeon.mind.liquid import DoubleBufferedNet
    from aeon.mind.trainer import SocietyTrainer
    d = SocietyDataset(tmp_path)
    acts = ["work", "feud", "study", "worship"]
    for i in range(n):
        d.append(_sample(action=acts[i % 4], feat=(i % 4) / 4.0))
    net = DoubleBufferedNet(hidden=32, layers=1, device="cpu")
    return SocietyTrainer(net, d, batch_size=64, min_samples=16, swap_every=3, **kw), d, net


def test_awr_identity_without_return_signal(tmp_path):
    tr, d, _ = _trainer(tmp_path)
    batch = [_sample().to_record() for _ in range(20)]      # no reward/advantage
    w = tr._awr_weights(batch, "cpu")
    assert torch.allclose(w, torch.ones(20))                # pure behavior cloning


def test_awr_weights_with_returns_are_bounded(tmp_path):
    tr, d, _ = _trainer(tmp_path)
    batch = [_sample(reward=r).to_record() for r in (-1.0, 0.0, 0.5, 2.0, 5.0)]
    w = tr._awr_weights(batch, "cpu")
    assert w.shape == (5,)
    assert torch.isfinite(w).all()
    assert float(w.min()) >= 0.25 - 1e-6 and float(w.max()) <= 4.0 + 1e-6
    # the highest-return sample is weighted more than the lowest
    assert float(w[-1]) > float(w[0])


def test_small_corpus_skips_validation(tmp_path):
    tr, d, _ = _trainer(tmp_path)        # 64 samples < min_for_split(256)
    for _ in range(60):
        tr.train_step()
    assert tr.validations == 0           # no split held out on a tiny corpus
    assert tr.status()["regression_drift_score"] == tr.drift_score


def test_large_corpus_runs_validation_and_drift(tmp_path):
    tr, d, _ = _trainer(tmp_path, n=700, min_for_split=256, val_every=10)
    for _ in range(120):
        tr.train_step()
    st = tr.status()
    assert tr.validations > 0
    assert st["best_val_capability"] >= st["val_capability"]
    assert st["val_drift"] >= 0.0
    assert "val_action_acc" in st


def test_balanced_sampling_trains(tmp_path):
    tr, d, net = _trainer(tmp_path, balance_actions=True)
    first = tr.train_step()["loss"]
    for _ in range(150):
        tr.train_step()
    assert tr.last_loss < first


def test_checkpoint_persistence(tmp_path):
    from aeon.mind.liquid import DoubleBufferedNet
    tr, d, net = _trainer(tmp_path)
    for _ in range(30):
        tr.train_step()
    ckpt = tmp_path / "ck.pt"
    net.save(ckpt)
    twin = DoubleBufferedNet(hidden=32, layers=1, device="cpu")
    assert twin.load(ckpt)
    assert twin.version == net.version
    # an incompatible checkpoint is rejected gracefully, not fatally
    big = DoubleBufferedNet(hidden=64, layers=2, device="cpu")
    assert big.load(ckpt) is False
