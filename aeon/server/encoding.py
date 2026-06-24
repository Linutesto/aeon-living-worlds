"""JSON encoding helpers — the boundary between the numpy-flavored simulation and
the strictly-typed world of JSON / WebSockets.

The simulation is full of numpy scalars (`np.float32`, `np.int64`, `np.bool_`) and
arrays. Worse, ``round(np.float32(x), 2)`` returns *another* ``np.float32`` — so even
"cleaned" values leak numpy types into payloads. Neither ``json.dumps`` (used by the
WebSocket) nor FastAPI's ``jsonable_encoder`` (used by REST) can encode those, and a
single leak aborts an entire payload — which is exactly what blanked the renderer.

``to_jsonable`` recursively converts any payload into plain JSON-safe Python, and
``CleanJSONResponse`` applies it for REST responses (bypassing ``jsonable_encoder`` by
returning a ``Response`` directly). Non-finite floats become ``null`` so the browser's
``JSON.parse`` never chokes on ``NaN``/``Infinity``.
"""

from __future__ import annotations

import math

import numpy as np
from fastapi.responses import JSONResponse


def to_jsonable(obj):
    """Recursively coerce numpy/exotic types into JSON-safe Python primitives."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {(k if isinstance(k, str) else str(k)): to_jsonable(v)
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):          # np.float32, np.int64, np.bool_, …
        return to_jsonable(obj.item())
    if isinstance(obj, np.ndarray):
        return to_jsonable(obj.tolist())
    return obj


class CleanJSONResponse(JSONResponse):
    """A JSONResponse that sanitizes numpy/non-finite values before encoding.

    Returning an instance of this from a route makes FastAPI skip
    ``jsonable_encoder`` entirely, so numpy scalars never reach the failing path.
    """

    def render(self, content) -> bytes:
        return super().render(to_jsonable(content))
