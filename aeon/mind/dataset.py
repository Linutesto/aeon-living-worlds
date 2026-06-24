"""SocietyDataset — the append-only training corpus, in the canonical format.

Every sample is one citizen-moment expressed in the format the spec asks for:

    INPUT  = world_state + citizen_profile + recent_events + relationship_graph
             (+ player_question, when one drove the sample)
    OUTPUT = action + emotion + memory_update + dialogue + future_intent

The 24-dim numeric `features` vector (PopulationManager.features) and any text
embeddings are stored *on the record* so training is self-contained and never has to
reach back into a live world. JSONL on disk is the source of truth; a bounded
in-memory ring buffer is what the trainer samples minibatches from (so a training step
never touches the filesystem). duckdb, if installed, is an optional index only.

Channels keep behavior distillation clean from borrowed reasoning traces:
  - "behavior"        teacher cohort outputs + mined AEON society events (trains the net)
  - "reasoning_style" filtered external reasoning traces (reserved corpus; not in the
                       behavior batch, so the "chaos" can't pollute citizen behavior)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("aeon.mind.dataset")

SCHEMA_VERSION = 1
SHARD_MAX_LINES = 50_000          # rotate JSONL shards so no file grows unbounded
BUFFER_MAX = 120_000              # in-memory records the trainer samples from


@dataclass
class Sample:
    """One citizen-moment in the training format."""

    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        r = asdict(self)
        r.setdefault("meta", {})
        r["meta"].setdefault("v", SCHEMA_VERSION)
        r["meta"].setdefault("ts", time.time())
        r["meta"].setdefault("channel", "behavior")
        return r


class SocietyDataset:
    def __init__(self, root: str | Path, *, buffer_max: int = BUFFER_MAX) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._shard_lines = 0
        self._shard_path: Path | None = None
        self.buffer: list[dict] = []          # recent records, for sampling
        self.buffer_max = buffer_max
        self.counts: dict[str, int] = {}      # channel -> total ever written
        self.total = 0
        self._seen_hashes: set[int] = set()    # cheap in-process dedupe
        self._load_existing()

    # ------------------------------------------------------------ persistence
    def _shards(self) -> list[Path]:
        return sorted(self.root.glob("samples_*.jsonl"))

    def _new_shard(self) -> Path:
        idx = len(self._shards())
        p = self.root / f"samples_{idx:05d}.jsonl"
        p.touch(exist_ok=True)
        self._shard_path = p
        self._shard_lines = sum(1 for _ in p.open()) if p.exists() else 0
        return p

    def _load_existing(self) -> None:
        """Seed the ring buffer + counters from the tail of existing shards."""
        shards = self._shards()
        if not shards:
            self._new_shard()
            return
        self._shard_path = shards[-1]
        self._shard_lines = sum(1 for _ in self._shard_path.open())
        # count everything (cheap line scan) but only buffer the tail
        tail: list[dict] = []
        for shard in shards:
            for line in shard.open():
                line = line.strip()
                if not line:
                    continue
                self.total += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ch = rec.get("meta", {}).get("channel", "behavior")
                self.counts[ch] = self.counts.get(ch, 0) + 1
                tail.append(rec)
                if len(tail) > self.buffer_max:
                    tail.pop(0)
        self.buffer = tail
        log.info("SocietyDataset loaded %d records (%s) from %s",
                 self.total, self.counts, self.root)

    # ------------------------------------------------------------------ write
    def append(self, sample: Sample | dict, *, dedupe_key: str | None = None) -> bool:
        rec = sample.to_record() if isinstance(sample, Sample) else dict(sample)
        if dedupe_key is not None:
            h = hash(dedupe_key)
            if h in self._seen_hashes:
                return False
            self._seen_hashes.add(h)
        ch = rec.get("meta", {}).get("channel", "behavior")
        with self._lock:
            if self._shard_path is None or self._shard_lines >= SHARD_MAX_LINES:
                self._new_shard()
            with self._shard_path.open("a") as fh:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            self._shard_lines += 1
            self.total += 1
            self.counts[ch] = self.counts.get(ch, 0) + 1
            self.buffer.append(rec)
            if len(self.buffer) > self.buffer_max:
                # drop oldest in bulk so this isn't O(n) every append
                self.buffer = self.buffer[len(self.buffer) - self.buffer_max:]
        return True

    def extend(self, samples) -> int:
        n = 0
        for s in samples:
            if self.append(s):
                n += 1
        return n

    # ----------------------------------------------------------------- sample
    def sample_batch(self, n: int, *, channel: str = "behavior", rng=None) -> list[dict]:
        """Random minibatch from the in-memory buffer (no disk I/O)."""
        import random as _random
        pool = [r for r in self.buffer
                if r.get("meta", {}).get("channel", "behavior") == channel]
        if not pool:
            return []
        chooser = (rng or _random)
        if len(pool) <= n:
            return list(pool)
        return chooser.sample(pool, n)

    def channel_size(self, channel: str = "behavior") -> int:
        return sum(1 for r in self.buffer
                   if r.get("meta", {}).get("channel", "behavior") == channel)

    def stats(self) -> dict:
        return {"total": self.total, "by_channel": dict(self.counts),
                "buffered": len(self.buffer), "shards": len(self._shards())}
