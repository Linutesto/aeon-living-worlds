"""Tests for the JSON sanitizer that fixed the renderer-blanking float32 bug."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from aeon.server.encoding import CleanJSONResponse, to_jsonable


def _dumpable(obj) -> str:
    """json.dumps with strict mode — raises ValueError on NaN/Inf, TypeError on
    non-native types. The thing every payload must survive."""
    return json.dumps(obj, allow_nan=False)


def test_numpy_scalars_become_native():
    out = to_jsonable({"f": np.float32(1.5), "i": np.int64(3), "b": np.bool_(True)})
    assert out == {"f": 1.5, "i": 3, "b": True}
    assert isinstance(out["f"], float) and isinstance(out["i"], int)
    assert isinstance(out["b"], bool)
    _dumpable(out)


def test_round_of_numpy_is_handled():
    # the actual bug: round(np.float32, n) returns np.float32, not float
    rounded = round(np.float32(0.123456), 2)
    assert isinstance(rounded, np.floating)          # confirms the trap exists
    assert isinstance(to_jsonable(rounded), float)   # ...and that we defuse it
    _dumpable(to_jsonable(rounded))


def test_numpy_arrays_and_nested():
    payload = {
        "elevation": np.round(np.linspace(-1, 1, 5).astype(np.float32), 3),
        "rows": [np.int32(1), np.int32(2)],
        "nested": {"v": [np.float32(0.5), {"deep": np.float64(0.25)}]},
    }
    out = to_jsonable(payload)
    assert isinstance(out["elevation"], list)
    assert all(isinstance(v, float) for v in out["elevation"])
    assert out["rows"] == [1, 2]
    assert out["nested"]["v"][1]["deep"] == 0.25
    _dumpable(out)


def test_non_finite_floats_become_null():
    out = to_jsonable({"a": float("nan"), "b": float("inf"), "c": np.float32("inf")})
    assert out == {"a": None, "b": None, "c": None}
    _dumpable(out)          # would raise if NaN/Inf survived


def test_clean_json_response_renders_numpy():
    resp = CleanJSONResponse({"x": np.float32(2.5), "n": [np.int64(7)]})
    body = resp.render({"x": np.float32(2.5), "n": [np.int64(7)]})
    assert json.loads(body) == {"x": 2.5, "n": [7]}


def test_set_and_tuple_become_list():
    out = to_jsonable({"s": {1, 2}, "t": (np.float32(1.0), 2)})
    assert sorted(out["s"]) == [1, 2]
    assert out["t"] == [1.0, 2]


def test_passthrough_primitives():
    assert to_jsonable("hi") == "hi"
    assert to_jsonable(None) is None
    assert to_jsonable(True) is True
    assert to_jsonable(42) == 42
