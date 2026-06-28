"""SocietyDataset — the append-only training corpus, in the canonical format.

Every sample is one citizen-moment expressed in the format the spec asks for:

    INPUT  = world_state + citizen_profile + recent_events + relationship_graph
             (+ player_question, when one drove the sample)
    OUTPUT = action + emotion + memory_update + dialogue + future_intent

The numeric `features` vector (PopulationManager.features: legacy person/city context
plus spatial observation features) and any text embeddings are stored *on the record*
so training is self-contained and never has to reach back into a live world. JSONL on
disk is the source of truth; a bounded
in-memory ring buffer is what the trainer samples minibatches from (so a training step
never touches the filesystem). duckdb, if installed, is an optional index only.

Channels keep behavior distillation clean from borrowed reasoning traces:
  - "behavior"        teacher cohort outputs + mined AEON society events (trains the net)
  - "reasoning_style" filtered external reasoning traces (reserved corpus; not in the
                       behavior batch, so the "chaos" can't pollute citizen behavior)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("aeon.mind.dataset")

SCHEMA_VERSION = 2
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
    def sample_batch(self, n: int, *, channel: str = "behavior", rng=None,
                     teacher_ratio: float | None = None,
                     prioritize_disagreement: bool = False,
                     split: str | None = None, val_fraction: float = 0.12,
                     balance_key: str | None = None) -> list[dict]:
        """Random minibatch from the in-memory buffer (no disk I/O).

        `split` ("train"/"val") draws from a deterministic, disjoint hash partition so
        validation is measured on data the optimizer never trains on. `balance_key`
        ("action") equalizes class frequency so a dominant action can't swamp the loss.
        """
        import random as _random
        pool = [r for r in self.buffer
                if r.get("meta", {}).get("channel", "behavior") == channel]
        if split in ("train", "val"):
            pool = [r for r in pool if _split_of(r, val_fraction) == split]
        if not pool:
            return []
        chooser = (rng or _random)
        if balance_key:
            return _balanced_sample(pool, n, chooser, balance_key)
        if teacher_ratio is not None:
            teacher_ratio = max(0.0, min(1.0, float(teacher_ratio)))
            teacher = [r for r in pool if r.get("meta", {}).get("source") in (
                "teacher", "teacher_correction")]
            student = [r for r in pool if r not in teacher]
            nt = min(len(teacher), int(round(n * teacher_ratio)))
            ns = min(len(student), max(0, n - nt))
            out = []
            if teacher:
                out.extend(_weighted_sample(teacher, nt, chooser, prioritize_disagreement))
            if student and ns:
                out.extend(_weighted_sample(student, ns, chooser, prioritize_disagreement))
            if len(out) < n:
                rest = [r for r in pool if r not in out]
                out.extend(_weighted_sample(rest, min(len(rest), n - len(out)),
                                            chooser, prioritize_disagreement))
            return out
        if len(pool) <= n:
            return list(pool)
        return _weighted_sample(pool, n, chooser, prioritize_disagreement)

    def channel_size(self, channel: str = "behavior", *, split: str | None = None,
                     val_fraction: float = 0.12) -> int:
        n = 0
        for r in self.buffer:
            if r.get("meta", {}).get("channel", "behavior") != channel:
                continue
            if split in ("train", "val") and _split_of(r, val_fraction) != split:
                continue
            n += 1
        return n

    def stats(self) -> dict:
        return {"total": self.total, "by_channel": dict(self.counts),
                "buffered": len(self.buffer), "shards": len(self._shards())}


def _split_of(rec: dict, val_fraction: float) -> str:
    """Stable, content-derived train/val assignment, memoized on the in-memory record.

    The same record always lands on the same side (so the optimizer never sees a val
    sample), but the partition is never written back to the JSONL on disk."""
    meta = rec.setdefault("meta", {})
    s = meta.get("_split")
    if s is None:
        sig = f"{meta.get('ts','')}|{meta.get('source','')}|" \
              f"{rec.get('output', {}).get('action','')}|{meta.get('city_id','')}"
        h = int(hashlib.md5(sig.encode()).hexdigest()[:8], 16)
        s = "val" if (h % 10_000) < int(max(0.0, min(1.0, val_fraction)) * 10_000) else "train"
        meta["_split"] = s
    return s


def _balanced_sample(pool: list[dict], n: int, chooser, key: str) -> list[dict]:
    """Class-balanced minibatch: draw roughly evenly across the values of `output[key]`
    so a dominant action can't dominate the gradient (replay-buffer balancing)."""
    if n <= 0 or not pool:
        return []
    buckets: dict[str, list[dict]] = {}
    for r in pool:
        cls = str(r.get("output", {}).get(key, "?"))
        buckets.setdefault(cls, []).append(r)
    classes = list(buckets)
    out: list[dict] = []
    # round-robin across classes (with replacement), drawing one random record per
    # class per pass until we have n — equalizes each class's expected representation.
    i = 0
    while len(out) < n:
        bucket = buckets[classes[i % len(classes)]]
        out.append(bucket[chooser.randrange(len(bucket))])
        i += 1
    return out


def _weighted_sample(pool: list[dict], n: int, chooser, prioritize: bool) -> list[dict]:
    if n <= 0 or not pool:
        return []
    if len(pool) <= n:
        return list(pool)
    if not prioritize:
        return chooser.sample(pool, n)
    weights = []
    for r in pool:
        meta = r.get("meta", {})
        w = 1.0
        if meta.get("source") == "teacher_correction":
            w += 3.0
        if meta.get("disagreement"):
            w += 2.0
        w += float(meta.get("priority", 0.0) or 0.0)
        weights.append(w)
    # `random.Random` has choices on supported Python versions; fall back if needed.
    if hasattr(chooser, "choices"):
        out = []
        seen = set()
        for rec in chooser.choices(pool, weights=weights, k=min(n * 3, len(pool) * 2)):
            key = id(rec)
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
            if len(out) >= n:
                return out
        if len(out) < n:
            out.extend([r for r in pool if id(r) not in seen][:n - len(out)])
        return out
    return chooser.sample(pool, n)
