"""The LLM scheduler: priority economy, budget, quotas, dedup, stale, fallbacks.

Verifies the call-economy guarantees: governor/teacher/interview (protected band) are
never starved, abundant low-priority reports/flavor are delayed/throttled, duplicate jobs
collapse, stale jobs cancel, and throttled low-priority work gets a cheap fallback.
"""

from __future__ import annotations

import asyncio
import json

from aeon.governor.arbiter import LLMScheduler


def _run(coro):
    return asyncio.run(coro)


def test_priority_orders_and_serializes_one_at_a_time():
    s = LLMScheduler()
    order: list[str] = []
    live = {"n": 0, "max": 0}

    async def work(name):
        live["n"] += 1; live["max"] = max(live["max"], live["n"])
        order.append(name)
        await asyncio.sleep(0.01)
        live["n"] -= 1

    async def main():
        async def blocker():
            order.append("blocker"); await asyncio.sleep(0.04)
        b = asyncio.create_task(s.run(blocker, consumer="flavor"))
        await asyncio.sleep(0.005)
        lo = [asyncio.create_task(s.run(lambda: work("flavor"), consumer="flavor"))
              for _ in range(3)]
        await asyncio.sleep(0.002)
        hi = asyncio.create_task(s.run(lambda: work("governor"), consumer="spirit_governor"))
        await asyncio.gather(b, hi, *lo)

    _run(main())
    assert live["max"] == 1                 # only one model call at a time
    assert order[0] == "blocker"
    assert order[1] == "governor"           # protected priority jumps the flavor queue


def test_interview_runs_under_report_pressure():
    s = LLMScheduler()
    order: list[str] = []

    async def work(name):
        order.append(name); await asyncio.sleep(0.01)

    async def main():
        async def blocker():
            order.append("blocker"); await asyncio.sleep(0.04)
        b = asyncio.create_task(s.run(blocker, consumer="world_report"))
        await asyncio.sleep(0.005)
        reports = [asyncio.create_task(s.run(lambda: work("report"), consumer="world_report"))
                   for _ in range(4)]
        await asyncio.sleep(0.002)
        iv = asyncio.create_task(s.run(lambda: work("interview"), consumer="citizen_interview"))
        await asyncio.gather(b, iv, *reports)

    _run(main())
    assert order[1] == "interview"          # a waiting human beats queued reports


def test_protected_priority_order_teacher_interview_governor():
    s = LLMScheduler()
    order: list[str] = []

    async def work(name):
        order.append(name)
        await asyncio.sleep(0.005)

    async def main():
        async def blocker():
            order.append("blocker")
            await asyncio.sleep(0.035)
        b = asyncio.create_task(s.run(blocker, consumer="flavor"))
        await asyncio.sleep(0.004)
        gov = asyncio.create_task(s.run(lambda: work("governor"), consumer="spirit_governor"))
        interview = asyncio.create_task(s.run(lambda: work("interview"), consumer="citizen_interview"))
        teacher = asyncio.create_task(s.run(lambda: work("teacher"), consumer="cohort_teacher"))
        await asyncio.gather(b, gov, interview, teacher)

    _run(main())
    assert order[:4] == ["blocker", "teacher", "interview", "governor"]


def test_dedup_collapses_identical_jobs():
    s = LLMScheduler()
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return "RESULT"

    async def main():
        return await asyncio.gather(
            s.run(fn, consumer="chronicle", cache_key="same"),
            s.run(fn, consumer="chronicle", cache_key="same"),
            s.run(fn, consumer="chronicle", cache_key="same"))

    out = _run(main())
    assert calls["n"] == 1                  # the real call happened once
    assert out == ["RESULT", "RESULT", "RESULT"]
    assert s.stats["chronicle"]["deduped"] >= 2


def test_stale_job_cancels_with_fallback():
    s = LLMScheduler()

    async def main():
        async def blocker():
            await asyncio.sleep(0.2)
        b = asyncio.create_task(s.run(blocker, consumer="flavor"))
        await asyncio.sleep(0.01)
        # this can't get a slot before max_wait → must give up with the fallback
        r = await s.run(lambda: asyncio.sleep(0), consumer="flavor",
                        fallback="(too late)", max_wait=0.03)
        b.cancel()
        return r

    assert _run(main()) == "(too late)"


def test_budget_throttles_low_priority_to_fallback():
    s = LLMScheduler(budget_per_min=10)        # absurdly small budget
    called = {"n": 0}

    async def fn():
        called["n"] += 1
        return "real"

    async def main():
        return await s.run(fn, consumer="flavor", tokens=1000, fallback="cheap")

    assert _run(main()) == "cheap"
    assert called["n"] == 0                  # never hit the model
    assert s.stats["flavor"]["skipped"] == 1


def test_protected_band_ignores_budget():
    s = LLMScheduler(budget_per_min=1)

    async def fn():
        return "spirit-spoke"

    async def main():
        return await s.run(fn, consumer="spirit_governor", tokens=99999)

    assert _run(main()) == "spirit-spoke"    # governor never throttled


def test_cooldown_throttles_repeat_calls():
    s = LLMScheduler()

    async def fn():
        return "x"

    async def main():
        a = await s.run(fn, consumer="world_report", cache_key="k1", fallback="fb")
        # immediate second call (different key to dodge dedup) is within the 20s cooldown
        b = await s.run(fn, consumer="world_report", cache_key="k2", fallback="fb")
        return a, b

    a, b = _run(main())
    assert a == "x" and b == "fb"
    assert s.throttle_reason.get("world_report") == "cooldown"


def test_status_and_history_serialize():
    s = LLMScheduler()

    async def main():
        await s.run(lambda: asyncio.sleep(0), consumer="cohort_teacher")
        await s.run(lambda: asyncio.sleep(0), consumer="flavor",
                    tokens=10, meta={"city": "Westcrag"})

    _run(main())
    st = s.status()
    json.dumps(st)                            # must be JSON-serializable
    assert st["labels"]["cohort_teacher"]["calls"] == 1
    assert "budget_remaining" in st and "most_starved" in st
    assert "calls_by_consumer" in st and st["calls_by_consumer"]["cohort_teacher"] == 1
    assert "latency_by_consumer" in st and "cohort_teacher" in st["latency_by_consumer"]
    assert "consumers" in st and "flavor" in st["consumers"]
    assert "token_budget_used" in st and "dropped_deferred" in st
    hist = s.recent(10)
    json.dumps(hist)
    assert any(h["consumer"] == "flavor" for h in hist)
