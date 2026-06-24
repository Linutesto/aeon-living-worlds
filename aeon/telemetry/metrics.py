"""Rolling time-series buffers for the dashboard charts.

Each named series is a fixed-length deque of (tick, value) samples. Sampled once per
sim tick (or downsampled by the engine) so historical trends are preserved without
unbounded growth.
"""

from __future__ import annotations

from collections import deque, defaultdict

# the series the dashboard expects to chart
SERIES = [
    "population", "biodiversity", "civilization_count", "city_count",
    "avg_temperature", "world_health", "species_count",
]


class Metrics:
    def __init__(self, window: int = 2000) -> None:
        self.window = window
        self._series: dict[str, "deque[tuple[int, float]]"] = defaultdict(
            lambda: deque(maxlen=window))

    def record(self, tick: int, snapshot: dict) -> None:
        for key in SERIES:
            if key in snapshot:
                self._series[key].append((tick, float(snapshot[key])))

    def series(self, key: str) -> list[float]:
        return [v for _, v in self._series.get(key, ())]

    def export(self) -> dict[str, list[list[float]]]:
        return {k: [[t, v] for t, v in s] for k, s in self._series.items()}

    def __getstate__(self):
        return {"window": self.window,
                "series": {k: list(v) for k, v in self._series.items()}}

    def __setstate__(self, state):
        self.window = state["window"]
        self._series = defaultdict(lambda: deque(maxlen=self.window))
        for k, values in state.get("series", {}).items():
            self._series[k] = deque(values, maxlen=self.window)
