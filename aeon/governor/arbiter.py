"""LLMScheduler — the call economy in front of the single local model server.

AEON has many model consumers (the world-spirit governor, the Chronicle, world-flavor,
two background-narration workers, on-demand interviews, and the 27B cohort teacher) all
hitting one Ollama on one GPU. Fired freely they thrash VRAM and the slow, valuable jobs
(teacher, governor, interviews) starve behind a flood of ambient reports/flavor.

This scheduler makes the scarce resource behave:

  * one gate, `max_concurrent` (default 1 — a single GPU can't parallelize generation);
  * **priority** classes, with a **protected band** (governor/teacher/interview) that is
    never throttled and that low-priority work can never preempt;
  * **starvation aging** so two low-priority classes don't deadlock each other;
  * per-consumer **cooldowns** and **quotas**, and a rolling **token budget** — when a
    low-priority consumer exceeds them it gets a cheap deterministic **fallback** instead
    of a real call;
  * **dedup** (identical cache_key jobs collapse onto one in-flight result);
  * **stale cancellation** (a job waiting past max_wait gives up with a fallback);
  * a **history** ring buffer recording every call/skip for observability.

Single-threaded asyncio means the in-memory bookkeeping needs no locks: it is only
mutated between `await` points.

Back-compat: the old name `LLMArbiter` and `run(fn, priority=, label=)` still work.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

# priority bands (lower wins) — kept as module constants for back-compat
TEACHER = 0
INTERVIEW = 1
GOVERNOR = 2
CHRONICLE = 6
FLAVOR = 8
NARRATION = 8

# consumer registry: name -> (priority, cooldown_s, quota_share of recent window)
CONSUMERS: dict[str, tuple[int, float, float]] = {
    "cohort_teacher":    (0, 0.0, 1.0),
    "citizen_interview": (1, 0.0, 1.0),
    "spirit_governor":   (2, 0.0, 1.0),
    "rare_citizen":      (3, 4.0, 0.30),
    "major_event":       (4, 4.0, 0.40),
    "world_report":      (5, 20.0, 0.25),
    "chronicle":         (6, 3.0, 0.40),
    "news":              (7, 10.0, 0.25),
    "flavor":            (8, 6.0, 0.25),
    "narration":         (8, 5.0, 0.30),
    "diagnostics":       (9, 30.0, 0.10),
}
PROTECTED = {"spirit_governor", "cohort_teacher", "citizen_interview"}
PROTECTED_CEILING = 3        # aging can lift a non-protected job no higher than this
AGE_STEP = 5.0               # seconds of waiting per +1 effective-priority step
DEFAULT_BUDGET = 60_000      # token budget per rolling 60s window
WINDOW = 60.0


class _Waiter:
    __slots__ = ("consumer", "priority", "seq", "enqueued", "event")

    def __init__(self, consumer, priority, seq):
        self.consumer = consumer
        self.priority = priority
        self.seq = seq
        self.enqueued = time.monotonic()
        self.event = asyncio.Event()

    def eff_priority(self, now: float, protected: bool) -> int:
        if protected:
            return self.priority
        bonus = int((now - self.enqueued) / AGE_STEP)
        return max(self.priority - bonus, PROTECTED_CEILING)


class LLMScheduler:
    def __init__(self, *, max_concurrent: int = 1, budget_per_min: int = DEFAULT_BUDGET):
        self.max_concurrent = max_concurrent
        self.budget_per_min = budget_per_min
        self._waiters: list[_Waiter] = []
        self._active = 0
        self._seq = 0
        self._pending: dict[str, asyncio.Future] = {}     # cache_key -> shared result
        self._last_call: dict[str, float] = {}            # consumer -> monotonic
        self._recent: deque[tuple[float, str, int]] = deque()  # (ts, consumer, tokens)
        self.inflight: list[str] = []
        self.stats: dict[str, dict] = {}                  # label -> counters
        self.history: deque[dict] = deque(maxlen=240)
        self.throttle_reason: dict[str, str] = {}         # consumer -> last reason

    # ----------------------------------------------------------- public API
    async def run(self, fn, *, consumer: str | None = None, priority: int | None = None,
                  label: str | None = None, cache_key: str | None = None,
                  tokens: int = 0, tick: int = 0, meta: dict | None = None,
                  fallback=None, max_wait: float | None = None):
        prio, cooldown, quota = self._resolve(consumer, priority)
        name = consumer or label or "llm"
        lbl = label or name
        protected = name in PROTECTED

        # dedup: attach to an in-flight identical job rather than issuing a second call
        if cache_key and cache_key in self._pending:
            self._record(lbl, name, prio, tick, meta, 0, deduped=True)
            return await self._pending[cache_key]

        # throttle non-protected consumers (return a cheap fallback if provided)
        if not protected:
            reason = self._throttle_reason(name, cooldown, quota, tokens)
            if reason:
                self.throttle_reason[name] = reason
                self._record(lbl, name, prio, tick, meta, 0, skipped=reason)
                return self._fallback_value(fallback)

        fut: asyncio.Future | None = None
        if cache_key:
            fut = asyncio.get_event_loop().create_future()
            self._pending[cache_key] = fut

        self._seq += 1
        w = _Waiter(name, prio, self._seq)
        self._waiters.append(w)
        self._dispatch()
        if not w.event.is_set():
            try:
                if max_wait:
                    await asyncio.wait_for(w.event.wait(), timeout=max_wait)
                else:
                    await w.event.wait()
            except asyncio.TimeoutError:
                if w in self._waiters:
                    self._waiters.remove(w)
                self._record(lbl, name, prio, tick, meta, 0, skipped="stale")
                val = self._fallback_value(fallback)
                self._settle(fut, cache_key, val)
                self._dispatch()
                return val

        # we hold a slot
        self.inflight.append(lbl)
        self._last_call[name] = time.monotonic()
        self._recent.append((time.monotonic(), name, tokens))
        t0 = time.monotonic()
        ok, result = True, None
        try:
            result = await fn()
            return result
        except Exception:
            ok = False
            raise
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            self._record(lbl, name, prio, tick, meta, ms, ok=ok,
                         out_tokens=_estimate(result))
            if lbl in self.inflight:
                self.inflight.remove(lbl)
            self._active -= 1
            self._settle(fut, cache_key, result if ok else None)
            self._dispatch()

    # ----------------------------------------------------------- internals
    def _resolve(self, consumer, priority):
        if consumer and consumer in CONSUMERS:
            return CONSUMERS[consumer]
        return (priority if priority is not None else 5), 0.0, 1.0

    def _dispatch(self) -> None:
        now = time.monotonic()
        while self._active < self.max_concurrent and self._waiters:
            w = min(self._waiters,
                    key=lambda x: (x.eff_priority(now, x.consumer in PROTECTED), x.seq))
            self._waiters.remove(w)
            self._active += 1
            w.event.set()

    def _throttle_reason(self, name, cooldown, quota, tokens) -> str | None:
        now = time.monotonic()
        while self._recent and now - self._recent[0][0] > WINDOW:
            self._recent.popleft()
        if cooldown and (now - self._last_call.get(name, -1e9)) < cooldown:
            return "cooldown"
        used = sum(t for _, _, t in self._recent)
        if used + tokens > self.budget_per_min:
            return "budget"
        if self._recent:
            share = sum(1 for _, c, _ in self._recent if c == name) / len(self._recent)
            if share > quota and len(self._recent) >= 6:
                return "quota"
        return None

    @staticmethod
    def _fallback_value(fallback):
        if callable(fallback):
            try:
                return fallback()
            except Exception:  # noqa: BLE001
                return ""
        return fallback if fallback is not None else ""

    def _settle(self, fut, cache_key, value) -> None:
        if fut is not None and not fut.done():
            fut.set_result(value)
        if cache_key:
            self._pending.pop(cache_key, None)

    def _record(self, label, consumer, priority, tick, meta, ms, *, ok=True,
                deduped=False, skipped=None, out_tokens=0) -> None:
        s = self.stats.setdefault(
            label, {"calls": 0, "errors": 0, "skipped": 0, "deduped": 0,
                    "last_ms": 0, "total_ms": 0})
        if skipped:
            s["skipped"] += 1
        elif deduped:
            s["deduped"] += 1
        else:
            s["calls"] += 1
            s["last_ms"] = ms
            s["total_ms"] += ms
            if not ok:
                s["errors"] += 1
        m = meta or {}
        self.history.append({
            "consumer": consumer, "priority": priority, "tick": tick,
            "ms": ms, "ok": ok, "skipped": skipped, "deduped": deduped,
            "out_tokens": out_tokens, "city": m.get("city"),
            "person": m.get("person"), "faction": m.get("faction"),
            "cache_key": m.get("cache_key"), "ts": round(time.monotonic(), 1),
        })

    # ----------------------------------------------------------- observability
    def status(self) -> dict:
        now = time.monotonic()
        while self._recent and now - self._recent[0][0] > WINDOW:
            self._recent.popleft()
        used = sum(t for _, _, t in self._recent)
        waiting = {}
        queue = []
        for w in self._waiters:
            waiting[w.consumer] = waiting.get(w.consumer, 0) + 1
            queue.append({
                "consumer": w.consumer,
                "priority": w.priority,
                "effective_priority": w.eff_priority(now, w.consumer in PROTECTED),
                "waited_s": round(now - w.enqueued, 1),
            })
        most_starved = None
        if self._waiters:
            oldest = max(self._waiters, key=lambda x: now - x.enqueued)
            most_starved = {"consumer": oldest.consumer,
                            "waited_s": round(now - oldest.enqueued, 1)}
        by_consumer: dict[str, dict] = {}
        for rec in self.history:
            consumer = rec.get("consumer", "unknown")
            row = by_consumer.setdefault(consumer, {
                "calls": 0, "errors": 0, "skipped": 0, "deduped": 0,
                "total_ms": 0, "latencies": [], "out_tokens": 0,
                "last_delay_reason": "",
            })
            if rec.get("skipped"):
                row["skipped"] += 1
                row["last_delay_reason"] = rec.get("skipped") or ""
            elif rec.get("deduped"):
                row["deduped"] += 1
            else:
                row["calls"] += 1
                if not rec.get("ok", True):
                    row["errors"] += 1
                ms = int(rec.get("ms", 0) or 0)
                row["total_ms"] += ms
                row["latencies"].append(ms)
                row["out_tokens"] += int(rec.get("out_tokens", 0) or 0)
        consumers = {}
        for name, row in by_consumer.items():
            calls = row["calls"]
            latencies = row["latencies"]
            consumers[name] = {
                "calls": calls,
                "errors": row["errors"],
                "skipped": row["skipped"],
                "deduped": row["deduped"],
                "avg_ms": int(row["total_ms"] / calls) if calls else 0,
                "p95_ms": int(sorted(latencies)[int(len(latencies) * 0.95) - 1]) if latencies else 0,
                "out_tokens": row["out_tokens"],
                "last_delay_reason": row["last_delay_reason"],
            }
        return {
            "inflight": list(self.inflight),
            "inflight_count": len(self.inflight),
            "queue_depth": len(self._waiters),
            "queue": sorted(queue, key=lambda r: (r["effective_priority"], -r["waited_s"]))[:24],
            "waiting": waiting,
            "max_concurrent": self.max_concurrent,
            "budget_per_min": self.budget_per_min,
            "tokens_used_1m": used,
            "token_budget_used": used,
            "budget_remaining": max(0, self.budget_per_min - used),
            "most_starved": most_starved,
            "throttle_reason": dict(self.throttle_reason),
            "dropped_deferred": sum(1 for h in self.history if h.get("skipped")),
            "calls_by_consumer": {k: v["calls"] for k, v in consumers.items()},
            "latency_by_consumer": {k: {"avg_ms": v["avg_ms"], "p95_ms": v["p95_ms"]}
                                    for k, v in consumers.items()},
            "consumers": consumers,
            "labels": {k: {"calls": v["calls"], "errors": v["errors"],
                           "skipped": v["skipped"], "deduped": v["deduped"],
                           "last_ms": v["last_ms"],
                           "avg_ms": int(v["total_ms"] / v["calls"]) if v["calls"] else 0}
                       for k, v in self.stats.items()},
        }

    def recent(self, n: int = 60) -> list[dict]:
        return list(self.history)[-n:]


def _estimate(text) -> int:
    return len(str(text)) // 4 if text else 0


# Back-compat alias for the earlier name.
LLMArbiter = LLMScheduler
