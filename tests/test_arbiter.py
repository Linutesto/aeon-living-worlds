"""The priority LLM arbiter: serialize all model calls, let the teacher preempt.

These verify the contention fix — that the expensive 27B cohort teacher can't be
starved by abundant cheap journaling.
"""

from __future__ import annotations

import asyncio

from aeon.governor import arbiter as arb
from aeon.governor.arbiter import LLMArbiter


def test_serializes_one_at_a_time():
    """No two calls run concurrently (a single GPU can't parallelize anyway)."""
    a = LLMArbiter()
    state = {"inflight": 0, "max": 0}

    async def call():
        state["inflight"] += 1
        state["max"] = max(state["max"], state["inflight"])
        await asyncio.sleep(0.02)
        state["inflight"] -= 1
        return "ok"

    async def main():
        await asyncio.gather(*[
            a.run(call, priority=arb.NARRATION, label="narration") for _ in range(8)])

    asyncio.run(main())
    assert state["max"] == 1
    assert a.stats["narration"]["calls"] == 8


def test_teacher_preempts_journaling():
    """With one slot busy, a waiting TEACHER call jumps ahead of queued journaling."""
    a = LLMArbiter()
    order: list[str] = []

    async def work(label):
        order.append(label)
        await asyncio.sleep(0.01)

    async def main():
        # occupy the arbiter so everything else must queue
        async def blocker():
            order.append("blocker")
            await asyncio.sleep(0.05)
        b = asyncio.create_task(a.run(blocker, priority=arb.GOVERNOR, label="governor"))
        await asyncio.sleep(0.01)            # ensure the blocker holds the slot
        # enqueue low-priority journaling first, then the high-priority teacher
        jobs = [
            asyncio.create_task(a.run(lambda: work("flavor"), priority=arb.FLAVOR, label="flavor")),
            asyncio.create_task(a.run(lambda: work("narration"), priority=arb.NARRATION, label="narration")),
        ]
        await asyncio.sleep(0.005)
        teacher = asyncio.create_task(
            a.run(lambda: work("teacher"), priority=arb.TEACHER, label="teacher"))
        await asyncio.gather(b, teacher, *jobs)

    asyncio.run(main())
    # teacher arrived last but, being top priority, runs before the queued journaling
    assert order[0] == "blocker"
    assert order[1] == "teacher"
    assert set(order[2:]) == {"flavor", "narration"}


def test_status_reports_labels():
    a = LLMArbiter()

    async def main():
        await a.run(lambda: asyncio.sleep(0), priority=arb.TEACHER, label="teacher")

    asyncio.run(main())
    st = a.status()
    assert st["labels"]["teacher"]["calls"] == 1
    assert "queue_depth" in st
