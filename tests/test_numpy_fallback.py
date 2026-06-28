"""Numpy-fallback parity for the species policy (focus area 3: keep numpy working).

The species policy ships a torch backend and a pure-numpy backend that must expose the
same interface and produce well-formed output regardless of which is active — so the
world stays alive (and tests pass) even where the ML stack is unavailable. Both backends
share the AWR math; this checks the contract, not bit-identical values.
"""

from __future__ import annotations

import numpy as np

from aeon.ai import species_policy as sp


def _feats(v=0.4):
    return [v] * sp.N_FEAT


def _batch(n=32):
    acts = sp.ACTIONS
    return [{"features": _feats((i % 5) / 5.0), "action": acts[i % len(acts)],
             "reward": (i % 5) / 5.0} for i in range(n)]


def test_numpy_backend_bias_shape_and_finite():
    pol = sp._NumpyPolicy()
    bias = pol.bias(_feats())
    assert len(bias) == sp.N_ACT
    assert np.isfinite(np.asarray(bias)).all()
    assert all(b >= 0 for b in bias)


def test_numpy_backend_learns_without_error():
    pol = sp._NumpyPolicy()
    loss = pol.learn(_batch())
    assert isinstance(loss, float)
    # interface still intact after an update
    assert len(pol.bias(_feats())) == sp.N_ACT


def test_coerce_features_pads_and_truncates():
    # both legacy-width and full-width feature vectors must be accepted
    assert len(sp._coerce_features([0.1] * sp.LEGACY_N_FEAT)) == sp.N_FEAT
    assert len(sp._coerce_features([0.1] * (sp.N_FEAT + 9))) == sp.N_FEAT
    assert len(sp._coerce_features([])) == sp.N_FEAT


def test_torch_and_numpy_share_interface():
    if not sp._TORCH:                      # torch absent → numpy is the only backend
        return
    t = sp._TorchPolicy()
    n = sp._NumpyPolicy()
    feats = _feats(0.3)
    bt, bn = t.bias(feats), n.bias(feats)
    assert len(bt) == len(bn) == sp.N_ACT
    assert np.isfinite(np.asarray(bt)).all() and np.isfinite(np.asarray(bn)).all()
    # both learn() return a float and keep a valid bias afterward
    assert isinstance(t.learn(_batch()), float)
    assert isinstance(n.learn(_batch()), float)
    assert len(t.bias(feats)) == len(n.bias(feats)) == sp.N_ACT
