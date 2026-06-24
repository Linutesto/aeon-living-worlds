"""Deterministic, named RNG streams.

Determinism is a hard requirement: the same seed plus the same sequence of governor
directives must always reproduce the same world. To keep independent subsystems from
disturbing each other's draws, every subsystem pulls from its *own* named stream
derived from the master seed.
"""

from __future__ import annotations

import hashlib

import numpy as np


def _derive(seed: int, name: str) -> int:
    """Stable 64-bit sub-seed for a named stream."""
    h = hashlib.blake2b(f"{seed}:{name}".encode(), digest_size=8)
    return int.from_bytes(h.digest(), "little")


class RNG:
    """A registry of independent numpy Generators keyed by subsystem name."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._streams: dict[str, np.random.Generator] = {}

    def stream(self, name: str) -> np.random.Generator:
        gen = self._streams.get(name)
        if gen is None:
            gen = np.random.default_rng(_derive(self.seed, name))
            self._streams[name] = gen
        return gen

    def chance(self, name: str, p: float) -> bool:
        """True with probability p, drawn from the named stream."""
        return bool(self.stream(name).random() < p)
