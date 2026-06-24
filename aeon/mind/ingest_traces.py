"""TraceIngester — fold *clean* external reasoning traces into the corpus.

These are optional borrowed reasoning/planning traces (e.g. `workspace/*/llm_calls.jsonl`,
keys task_type/model/messages/thinking/response). They are a "reasoning style" corpus,
NOT citizen behavior — so they land on the dedicated `reasoning_style` channel and the
behavior trainer never samples them. The whole point of the user's warning ("don't
distill the chaos") is the filter below: empty/errored/garbage responses are dropped,
duplicates are hashed away, and a per-run cap keeps a flood of logs from swamping the
real society data.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .dataset import Sample, SocietyDataset
from .encode import get_embedder

log = logging.getLogger("aeon.mind.ingest")

# Optional external reasoning-trace corpora to seed the society dataset. Empty by
# default; point these at your own JSONL trace dirs via config (mind.trace_paths) if you
# want to warm-start the student. See docs/ARCHITECTURE.md.
DEFAULT_PATHS: list[str] = []
DEFAULT_ALLOWLIST = [
    "orchestrator", "planner", "reasoning", "synthesis", "summarize",
    "analysis", "research", "reflect", "decompose", "content",
]
_ERROR_MARKERS = ("traceback", "exception:", "error:", "<empty>", "null", "none",
                  "i cannot", "as an ai")
MIN_RESP_CHARS = 40
MAX_RESP_CHARS = 8000


def _clean(resp: str, task_type: str, allowlist: set[str]) -> bool:
    if not resp or not resp.strip():
        return False
    low = resp.strip().lower()
    if len(low) < MIN_RESP_CHARS or len(resp) > MAX_RESP_CHARS:
        return False
    if any(m in low[:200] for m in _ERROR_MARKERS):
        return False
    # allow if the task type is whitelisted, or unknown-but-substantive
    if allowlist and task_type and task_type not in allowlist:
        return False
    return True


def _question_of(messages) -> str:
    """Best-effort extract the prompting question from a messages list/blob."""
    if isinstance(messages, str):
        return messages[:400]
    if isinstance(messages, list):
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                return str(m.get("content", ""))[:400]
    return ""


class TraceIngester:
    def __init__(self, dataset: SocietyDataset, *, paths=None, allowlist=None,
                 max_samples: int = 2000, embedder=None) -> None:
        self.dataset = dataset
        self.paths = [Path(p) for p in (paths or DEFAULT_PATHS)]
        self.allowlist = set(allowlist if allowlist is not None else DEFAULT_ALLOWLIST)
        self.max_samples = max_samples
        self.embedder = embedder or get_embedder()

    def _files(self):
        for base in self.paths:
            if not base.exists():
                continue
            if base.is_file() and base.suffix == ".jsonl":
                yield base
            else:
                yield from sorted(base.rglob("llm_calls.jsonl"))

    def run(self) -> dict:
        added = scanned = rejected = 0
        for fp in self._files():
            if added >= self.max_samples:
                break
            try:
                lines = fp.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            for line in lines:
                if added >= self.max_samples:
                    break
                line = line.strip()
                if not line:
                    continue
                scanned += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    rejected += 1
                    continue
                resp = str(row.get("response", "") or "")
                task = str(row.get("task_type", "") or "")
                if not _clean(resp, task, self.allowlist):
                    rejected += 1
                    continue
                key = hashlib.md5(resp[:512].encode()).hexdigest()
                sample = Sample(
                    input={"world_state": {}, "citizen_profile": None,
                           "recent_events": [], "relationship_graph": None,
                           "player_question": _question_of(row.get("messages"))},
                    output={"action": None, "emotion": None, "memory_update": None,
                            "dialogue": resp[:2000], "future_intent": None},
                    meta={"channel": "reasoning_style", "source": "trace",
                          "model": row.get("model"), "task_type": task,
                          "dialogue_emb": self.embedder.embed(resp[:2000]),
                          "embed_kind": self.embedder.kind, "origin": str(fp)},
                )
                if self.dataset.append(sample, dedupe_key=key):
                    added += 1
        result = {"added": added, "scanned": scanned, "rejected": rejected}
        log.info("TraceIngester: %s", result)
        return result
