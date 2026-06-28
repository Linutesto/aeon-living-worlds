"""Tests for configurable liquid-student sizes (focus area 2).

Guards the named size presets (tiny/small/medium/large), that they land in the spec's
parameter bands and grow monotonically, that a named size or raw hidden/layers both
resolve correctly, and that a checkpoint round-trips the size.
"""

from __future__ import annotations

import pytest

from aeon.mind import encode as enc
from aeon.mind.liquid import (MODEL_SIZES, dims_for_size, resolve_dims)

torch = pytest.importorskip("torch")

# (low, high) parameter band each named size must fall inside (±~40% around the target).
_BANDS = {"tiny": (0.45e6, 1.0e6), "small": (2.2e6, 3.8e6),
          "medium": (8.0e6, 12.5e6), "large": (12.0e6, 18.0e6)}


def _params(size: str) -> int:
    from aeon.mind.liquid import LiquidSocietyNet
    h, l = dims_for_size(size)
    return LiquidSocietyNet(hidden=h, layers=l).n_params()


def test_all_sizes_present():
    assert set(MODEL_SIZES) == {"tiny", "small", "medium", "large"}


def test_sizes_hit_their_bands():
    for size, (lo, hi) in _BANDS.items():
        n = _params(size)
        assert lo <= n <= hi, f"{size} has {n/1e6:.2f}M params, want {lo/1e6}-{hi/1e6}M"


def test_sizes_are_monotonic():
    order = [_params(s) for s in ("tiny", "small", "medium", "large")]
    assert order == sorted(order)
    assert order[0] != order[-1]


def test_resolve_dims_named_and_explicit():
    assert resolve_dims(size="medium") == MODEL_SIZES["medium"]
    # explicit hidden/layers override the named size's individual dims
    h, l = resolve_dims(size="tiny", hidden=512)
    assert h == 512 and l == MODEL_SIZES["tiny"][1]
    # unknown size falls back to tiny, not a crash
    assert resolve_dims(size="enormous") == MODEL_SIZES["tiny"]


def test_double_buffer_carries_size_and_checkpoints_it(tmp_path):
    from aeon.mind.liquid import DoubleBufferedNet
    net = DoubleBufferedNet(size="small", device="cpu")
    assert net.size == "small"
    assert (net.hidden, net.layers) == MODEL_SIZES["small"]
    path = tmp_path / "student.pt"
    net.save(path)
    blob = torch.load(path, map_location="cpu", weights_only=False)
    assert blob["size"] == "small"
    # a same-size net loads the checkpoint
    twin = DoubleBufferedNet(size="small", device="cpu")
    assert twin.load(path)
    assert twin.version == net.version


def test_in_dim_drives_net_input():
    # the net is built from the encoder's IN_DIM so widening spatial features stays safe
    from aeon.mind.liquid import LiquidSocietyNet
    net = LiquidSocietyNet(hidden=32, layers=1)
    x = torch.randn(2, enc.SEQ_LEN, enc.IN_DIM)
    dt = torch.ones(2, enc.SEQ_LEN)
    heads, _ = net(x, dt)
    assert heads["action"].shape == (2, enc.N_ACTION)
