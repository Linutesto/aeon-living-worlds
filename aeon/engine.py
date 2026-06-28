"""The orchestrator. Owns the world and runs two decoupled async loops:

  * sim loop      — advances the deterministic world at `sim.tick_seconds`,
                    scaled by the dashboard speed multiplier (pause..100x).
  * governor loop — wakes every `governor.tick_seconds`, asks the spirit to
                    deliberate, applies directives.

It also serves as the single read/serialize surface for the server: the broadcaster
and REST routes call into the engine, never into the sim directly. State is
serialized into JSON-friendly dicts here so the transport layer stays dumb.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Any

import numpy as np

from .config import Config, ROOT
from .sim import world as world_mod
from .sim import cities as city_mod
from .sim import civilization as civ_mod
from .sim import events as sim_events
from .sim import species as sim_species
from .sim.worldgen import WorldGenConfig, LAYERS
from .governor.governor import Governor
from .governor.memory import GovernorMemory
from .governor.directives import Directive, apply as apply_directive
from .telemetry.history import History
from .telemetry.metrics import Metrics
from .telemetry import stats as stats_mod
from .agents.population import PopulationManager
from .agents import spatial as spatial_mod
from .agents import interview as interview_mod
from .agents import schedule as schedule_mod
from .sim import season as season_mod
from .ai.species_policy import SpeciesBrain
from .governor.llm import LLMClient
from .governor import arbiter as arb
from .governor.arbiter import LLMArbiter
from .society import Society
from .society import chronicle as chronicle_mod
from .society import flavor as flavor_mod
from .society import interpret as interpret_mod
from .society.flavor import FlavorStore, FlavorPiece
from .persistence import SaveStore
from .observer import ObserverState
from .society.religion import Religion
from .society.faction import Faction, _GOAL as FACTION_GOAL

log = logging.getLogger("aeon.engine")
MEMORY_PATH = ROOT / "world_memory.json"
CHRONICLE_PATH = ROOT / "world_chronicle.json"
FLAVOR_PATH = ROOT / "world_flavor.json"
INTERP_PATH = ROOT / "world_interp.json"


class Engine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.world = world_mod.create_world(cfg)
        self.history = History(cfg.telemetry.history_max_events)
        self.metrics = Metrics(cfg.telemetry.metrics_window)
        self.memory = GovernorMemory.load(MEMORY_PATH)
        self.governor = Governor(cfg.governor, self.world, self.memory,
                                 self.history, self.metrics)
        # one scheduler (priority + budget + quotas + dedup) in front of the single
        # local model server, so cheap abundant journaling can't starve the governor,
        # 27B teacher, or interviews. The governor rides the protected band.
        self.llm_arbiter = LLMArbiter(
            max_concurrent=getattr(cfg.governor, "llm_max_concurrent", 1),
            budget_per_min=getattr(cfg.governor, "llm_budget_per_min", 60_000))
        self.governor.llm.arbiter = self.llm_arbiter
        self.governor.llm.default_priority = arb.GOVERNOR
        self.governor.llm.default_label = "spirit_governor"   # protected band
        self.governor.llm.keep_alive = getattr(cfg.governor, "keep_alive", None)

        # individual layer: the LOD persona pool + per-species learning policies
        self.population = PopulationManager(cfg)
        self.world.species_brain = SpeciesBrain()

        # the Society Intelligence Stack: 27B teacher cohort inference → live liquid
        # student. Constructed lazily so a torch-less environment still runs.
        self.society_mind = None
        self.teacher = None
        self._teacher_llm = None
        self._mind_train_warned = False

        # emergent society: religions, factions, and the LLM-written chronicle
        self.society = Society()
        self.society.chronicle.load(CHRONICLE_PATH)
        self.observer = ObserverState()

        # async, cached, rate-limited world flavor (rumors, news, journals, …)
        self.flavor = FlavorStore()
        self.flavor.load(FLAVOR_PATH)

        # the LLM interpretation layer (biographies, newspaper) — cached, grounded
        self.interp = interpret_mod.Cache()
        self.interp.load(INTERP_PATH)
        self._newspaper = {"tick": -10_000, "items": ""}
        self._flavor_rng = random.Random(cfg.world.seed ^ 0xF1A)
        self._llm_jobs: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._llm_job_sigs: set[str] = set()
        self._llm_history_cursor = 0
        self._llm_last_discovery_tick = -10_000
        self._llm_recent: list[dict[str, Any]] = []
        self._llm_rate_lock = asyncio.Lock()
        self._llm_last_at = 0.0

        self.speed: float = 1.0          # 0 == paused; dashboard time control
        self.running = True
        self.sim_tick_ms = 0.0      # rolling CPU cost of one sim tick (perf HUD)
        # presentation choices (cosmetic; persisted + shipped to the renderer)
        self.graphics_preset = "desktop"
        self.texture_pack = "default-clean"
        self.render_budgets: dict[str, Any] = {
            "lod_distance": 1.0, "max_buildings": 18000, "max_particles": 6000,
            "max_lights": 800, "city_density": 1.0, "building_density": 1.0,
            "road_density": 1.0,
        }
        self.restart_count = 0
        self.restart_meta: dict[str, Any] = {
            "count": 0, "seed": cfg.world.seed, "parent_seed": None,
            "kept_minds": False, "reset_layers": []}
        self.save_store = SaveStore()
        self.last_save: dict[str, Any] | None = None
        self._last_autosave_tick = 0
        self._policy_events: list[dict] = []
        self._experience_cursor = 0
        self._last_policy_tick = 0
        self.last_stats: dict = {}
        self.last_governor: dict = {"thought": "(the spirit has not yet woken)",
                                    "goal": self.memory.current_goal,
                                    "directives": [], "online": None}
        self._tasks: list[asyncio.Task] = []
        self.history.add({"tick": 0, "type": "event", "kind": "genesis",
                          "title": "The world began", "detail": cfg.world.name})
        # founding of the genesis nations (seeded in create_world) joins the timeline
        self.history.extend(getattr(self.world, "genesis_events", []))
        if cfg.persistence.enabled and cfg.persistence.autosave_on_boot:
            self._load_autosave_if_present()
        self._llm_history_cursor = self._latest_history_id()
        self._init_society_mind()

    # ---------------- society intelligence stack ----------------
    def _init_society_mind(self, *, load_weights: bool = True) -> None:
        """Build the HybridMind + 27B teacher, seed the corpus from filtered traces.

        Anything missing (torch, the mind disabled in config) leaves society_mind None
        and the world runs exactly as before. `load_weights=False` builds a *fresh*
        student (used by a restart that resets minds rather than carrying them over)."""
        if self._teacher_llm is not None:        # restart path: drop the old client
            try:
                import asyncio as _a
                _a.get_event_loop().create_task(self._teacher_llm.aclose())
            except Exception:  # noqa: BLE001
                pass
            self._teacher_llm = None
        mcfg = getattr(self.cfg, "mind", None)
        if mcfg is None or not mcfg.enabled:
            return
        try:
            from .mind.runtime import HybridMind
        except Exception as e:  # noqa: BLE001 — torch absent → mind simply off
            log.warning("Society mind unavailable (%s); running without it.", e)
            return
        ddir = ROOT / mcfg.dataset_dir if not Path(mcfg.dataset_dir).is_absolute() \
            else Path(mcfg.dataset_dir)
        self.society_mind = HybridMind(mcfg, dataset_dir=ddir, society=self.society)
        self.world.society_mind = self.society_mind
        if load_weights:
            self.society_mind.load(self.save_store.weights_path(mcfg.weights_slot))

        # a dedicated big-model client for cohort inference (governor stays on its 2B)
        import dataclasses
        tcfg = dataclasses.replace(self.cfg.governor, model=mcfg.teacher_model,
                                   max_tokens=mcfg.teacher_max_tokens,
                                   timeout_seconds=mcfg.teacher_timeout)
        # highest priority + a long keep_alive so the 27B stays warm in VRAM between
        # cohorts (no 30s reload each time) and preempts journaling.
        self._teacher_llm = LLMClient(tcfg, arbiter=self.llm_arbiter,
                                      default_priority=arb.TEACHER,
                                      default_label="cohort_teacher",
                                      keep_alive=getattr(mcfg, "keep_alive", "20m"),
                                      num_ctx=getattr(mcfg, "teacher_num_ctx", None))
        from .mind.teacher import TeacherInference
        self.teacher = TeacherInference(
            self._teacher_llm, self.society_mind.dataset,
            batcher=self.society_mind.batcher, model=mcfg.teacher_model)

        if mcfg.ingest_traces:
            try:
                from .mind.ingest_traces import TraceIngester
                TraceIngester(self.society_mind.dataset, paths=mcfg.trace_paths,
                              max_samples=mcfg.trace_max_samples).run()
            except Exception:  # noqa: BLE001
                log.exception("trace ingestion failed (non-fatal)")
        log.info("Society mind online: %s", self.society_mind.status().get("backend"))

    # ---------------- lifecycle ----------------
    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._sim_loop(), name="sim"),
            asyncio.create_task(self._governor_loop(), name="governor"),
            asyncio.create_task(self._mind_loop(), name="mind"),
            asyncio.create_task(self._chronicler_loop(), name="chronicler"),
            asyncio.create_task(self._flavor_loop(), name="flavor"),
            asyncio.create_task(self._background_narration_watch_loop(),
                                name="background-narration-watch"),
            asyncio.create_task(self._background_narration_worker_loop(0),
                                name="background-narration-0"),
            asyncio.create_task(self._background_narration_worker_loop(1),
                                name="background-narration-1"),
        ]
        if self.society_mind is not None:
            self._tasks.append(
                asyncio.create_task(self._cohort_loop(), name="mind-cohort"))
            self._tasks.append(
                asyncio.create_task(self._society_train_loop(), name="mind-train"))

    async def stop(self) -> None:
        self.running = False
        for t in self._tasks:
            t.cancel()
        await self.governor.aclose()
        if self._teacher_llm is not None:
            await self._teacher_llm.aclose()
        if self.society_mind is not None:
            try:
                self.society_mind.save(
                    self.save_store.weights_path(self.cfg.mind.weights_slot))
            except Exception:  # noqa: BLE001
                log.exception("failed to save society mind weights")
        self.memory.save(MEMORY_PATH)
        self.society.chronicle.save(CHRONICLE_PATH)
        self.flavor.save(FLAVOR_PATH)
        self.interp.save(INTERP_PATH)

    # ---------------- loops ----------------
    async def _sim_loop(self) -> None:
        """Real-time-decoupled tick loop. The loop wakes at a fixed `loop_hz`; the
        UI speed multiplier scales how many sim ticks elapse per real second
        (`speed * base_tps`). A fractional accumulator means sub-1x speeds (x0.25,
        x0.5) genuinely run slower — a tick fires only every few wakes — while high
        speeds run a bounded batch per wake so the loop never stalls."""
        sim = self.cfg.sim
        interval = 1.0 / max(1.0, sim.loop_hz)
        accumulator = 0.0
        while self.running:
            await asyncio.sleep(interval)
            if self.speed <= 0:
                accumulator = 0.0            # a true pause: no debt accrues
                continue
            accumulator += self.speed * sim.base_tps * interval
            steps = int(accumulator)
            if steps <= 0:
                continue                     # slow mode: not enough time for a tick yet
            accumulator -= steps
            steps = min(steps, sim.max_steps_per_wake)
            _tick_t0 = time.perf_counter()
            for _ in range(steps):
                events = world_mod.tick(self.world)
                if events:
                    self.history.extend(events)
                    self._collect_policy_events(events)
                # advance the lives of focused individuals; record their stories
                life = self.population.tick(self.world)
                if life:
                    self.history.extend(life)
                    self._collect_policy_events(life)
                # when a life-tick actually ran, let emergent society move too
                if self.population._last_life_tick == self.world.tick:
                    soc = self.society.step(self.world, self.population)
                    if soc:
                        self.history.extend(soc)
                        self._collect_policy_events(soc)
                    self._collect_periodic_policy_samples()
            # average wall-time per sim tick this wake, for the renderer's perf HUD
            self.sim_tick_ms = round((time.perf_counter() - _tick_t0) * 1000 / steps, 2)
            self.metrics.record(self.world.tick, self._cheap_stats())
            self._maybe_autosave()

    async def _mind_loop(self) -> None:
        """Level-2 learning: periodically train the per-species policies on how
        their people have fared. Runs off the hot path."""
        while self.running:
            await asyncio.sleep(8.0)
            try:
                if self.speed <= 0:
                    continue
                if self.world.tick - self._last_policy_tick < 48:
                    continue
                self._last_policy_tick = self.world.tick
                new_life = self.population.experience[self._experience_cursor:]
                self._experience_cursor = len(self.population.experience)
                samples = new_life + self._policy_events
                self._policy_events = []
                if samples:
                    self.world.species_brain.learn(samples)
            except Exception:  # noqa: BLE001
                log.exception("species learning step failed")

    async def _cohort_loop(self) -> None:
        """The teacher: every `cohort_interval`s the 27B reasons over a whole cohort of
        citizens (crisis cities first) in ONE call; outputs enrich their inner life and
        become training samples. Rate-limited so it shares the GPU with the spirit."""
        mcfg = self.cfg.mind
        await asyncio.sleep(10.0)
        while self.running:
            await asyncio.sleep(max(4.0, float(mcfg.cohort_interval)))
            if self.speed <= 0 or self.teacher is None:
                continue
            try:
                # the arbiter serializes + prioritizes (TEACHER preempts journaling);
                # no extra lock needed here.
                res = await self.teacher.run(self.world, self.population,
                                             self.society, rng=self._flavor_rng)
                if res.get("ran"):
                    log.info("cohort taught: %s", res)
            except Exception:  # noqa: BLE001
                log.exception("cohort teacher step failed")

    async def _society_train_loop(self) -> None:
        """The student: continuous background distillation on the GPU. Each step runs
        in a worker thread so backprop never blocks the sim loop; weights publish to the
        serving net inside the trainer. This is what makes the idle 4090 sweat."""
        mcfg = self.cfg.mind
        await asyncio.sleep(6.0)
        since_save = 0
        while self.running:
            await asyncio.sleep(max(0.05, float(mcfg.train_interval)))
            if self.society_mind is None:
                return
            try:
                metrics = await asyncio.to_thread(self.society_mind.train_step)
                if metrics:
                    since_save += 1
                    if since_save >= 400:
                        since_save = 0
                        self.society_mind.save(
                            self.save_store.weights_path(mcfg.weights_slot))
            except Exception:  # noqa: BLE001
                if not self._mind_train_warned:
                    log.exception("society student training step failed")
                    self._mind_train_warned = True

    async def _chronicler_loop(self) -> None:
        """Event-driven history: when a major event has queued, ask the LLM to set
        it down in the Chronicle. Rate-limited — rich language only when earned."""
        while self.running:
            await asyncio.sleep(6.0)
            queue = self.society.pending_chronicle
            if not queue:
                continue
            event = queue.pop(0)
            try:
                # cheap deterministic line if the scheduler throttles low-priority prose
                fallback = f"{event.get('title', 'An event')} came to pass in these years."
                text = await self.governor.llm.complete(
                    chronicle_mod.SYSTEM,
                    chronicle_mod.build_prompt(event, self.world),
                    format_json=False, consumer="chronicle",
                    cache_key=f"chron:{event.get('title','')}:{event.get('type','')}",
                    tick=self.world.tick, fallback=fallback,
                    meta={"city": event.get("title", "")})
                self.society.chronicle.add(self.world.tick, event.get("type", "event"),
                                           event.get("title", ""), text)
            except Exception:  # noqa: BLE001
                log.exception("chronicler failed")

    async def _flavor_loop(self) -> None:
        """Async, rate-limited world flavor: one short LLM-written piece (rumor,
        news, journal, sermon, obituary…) every `interval` seconds, chosen from
        eventful/focused cities and cached. Never touches the simulation; if the
        model is offline it simply produces nothing."""
        interval = max(6.0, float(self.cfg.governor.flavor_interval))
        await asyncio.sleep(8.0)
        while self.running:
            await asyncio.sleep(interval)
            if self.speed <= 0 or not self.cfg.governor.enabled:
                continue
            try:
                # obituaries for the recently, notably dead take priority
                subject = self._pending_obituary()
                if subject is not None:
                    city, kind, extra = subject
                else:
                    city, kind = flavor_mod.pick_subject(self, self._flavor_rng)
                    extra = ""
                if city is None:
                    continue
                prompt = flavor_mod.build_prompt(self, city, kind) + extra
                text = await self.governor.llm.complete(
                    flavor_mod.SYSTEM, prompt, format_json=False,
                    consumer="flavor", tick=self.world.tick,
                    cache_key=f"flavor:{city.id}:{kind}:{self.world.tick // 50}",
                    meta={"city": city.name})
                if text and not text.startswith("(…"):
                    self.flavor.add(FlavorPiece(
                        tick=self.world.tick, kind=kind, city_id=city.id,
                        city=city.name, text=text.strip()))
            except Exception:  # noqa: BLE001
                log.exception("flavor generation failed")

    async def _background_narration_watch_loop(self) -> None:
        """Watch real simulation events and enqueue grounded interpretation jobs.

        This is intentionally a consumer: it never mutates sim state and it only
        builds fact sheets from current world/history records.
        """
        await asyncio.sleep(5.0)
        while self.running:
            await asyncio.sleep(5.0)
            if self.speed <= 0 or not self.cfg.governor.enabled:
                continue
            try:
                for ev in self.history.since_id(self._llm_history_cursor):
                    self._llm_history_cursor = max(self._llm_history_cursor, ev.get("id", 0))
                    self._enqueue_event_narration(ev)
                if self.world.tick - self._llm_last_discovery_tick >= 240:
                    self._llm_last_discovery_tick = self.world.tick
                    self._enqueue_discovery_narrations()
            except Exception:  # noqa: BLE001
                log.exception("background narration watcher failed")

    async def _background_narration_worker_loop(self, worker_id: int) -> None:
        """Global rate-limited queue for proactive LLM interpretation."""
        while self.running:
            job = await self._llm_jobs.get()
            try:
                await self._rate_limit_background_narration()
                r = await self._narrate(
                    job["kind"], job["key"], job["sig"], job["system"], job["facts"])
                if r.get("text"):
                    self._llm_recent.append({
                        "tick": self.world.tick, "kind": job["kind"],
                        "key": job["key"], "cached": bool(r.get("cached")),
                        "worker": worker_id,
                    })
                    self._llm_recent = self._llm_recent[-80:]
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("background narration worker failed")
            finally:
                self._llm_jobs.task_done()

    async def _rate_limit_background_narration(self) -> None:
        interval = max(8.0, float(self.cfg.governor.flavor_interval) * 0.75)
        async with self._llm_rate_lock:
            wait = interval - (time.monotonic() - self._llm_last_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._llm_last_at = time.monotonic()

    def _enqueue_event_narration(self, ev: dict) -> None:
        job = self._narration_job_for_event(ev)
        if job:
            self._enqueue_narration_job(job)

    def _enqueue_discovery_narrations(self) -> None:
        for d in self.discoveries().get("discoveries", [])[:8]:
            value = d.get("value")
            if isinstance(value, (int, float)) and value <= 0:
                continue
            sig = interpret_mod.discovery_signature(d)
            if self.interp.get("discovery", d.get("key"), sig):
                continue
            self._enqueue_narration_job({
                "kind": "discovery", "key": d.get("key"), "sig": sig,
                "system": interpret_mod.DISCOVERY_SYSTEM,
                "facts": interpret_mod.build_discovery_facts(self.world, d),
            })

    def _enqueue_narration_job(self, job: dict[str, Any]) -> bool:
        sig_key = f"{job['kind']}:{job['key']}:{job['sig']}"
        if sig_key in self._llm_job_sigs:
            return False
        if self.interp.get(job["kind"], job["key"], job["sig"]):
            return False
        try:
            self._llm_jobs.put_nowait(job)
        except asyncio.QueueFull:
            return False
        self._llm_job_sigs.add(sig_key)
        if len(self._llm_job_sigs) > 3000:
            self._llm_job_sigs = set(list(self._llm_job_sigs)[-1500:])
        return True

    def _narration_job_for_event(self, ev: dict) -> dict[str, Any] | None:
        typ = ev.get("type")
        if typ == "settlement" and ev.get("city_id") is not None:
            c = self.world.cities.get(ev.get("city_id"))
            if not c or not c.alive:
                return None
            civ = self.world.civilizations.get(c.civ_id)
            rel, share = self.society.religion_of_city(c.id)
            chron = self.city_chronicle(c)
            return {
                "kind": "city", "key": c.id,
                "sig": interpret_mod.city_signature(c, len(chron)),
                "system": interpret_mod.CITY_SYSTEM,
                "facts": interpret_mod.build_city_facts(c, self.world, civ, rel, share, chron),
            }
        if typ in ("war", "collapse", "revolution") and ev.get("city_id") is not None:
            c = self.world.cities.get(ev.get("city_id"))
            if not c:
                return None
            civ = self.world.civilizations.get(c.civ_id)
            rel, share = self.society.religion_of_city(c.id)
            chron = self.city_chronicle(c)
            return {
                "kind": "city", "key": c.id,
                "sig": interpret_mod.city_signature(c, len(chron)),
                "system": interpret_mod.CITY_SYSTEM,
                "facts": interpret_mod.build_city_facts(c, self.world, civ, rel, share, chron),
            }
        if typ in ("religion_founded", "schism", "holy_war") and ev.get("religion_id") is not None:
            rel = self.society.religions.get(ev.get("religion_id"))
            if not rel or not rel.alive:
                return None
            followers = rel.follower_estimate(self.world)
            return {
                "kind": "relig", "key": rel.id,
                "sig": interpret_mod.religion_signature(rel, followers),
                "system": interpret_mod.RELIGION_SYSTEM,
                "facts": interpret_mod.build_religion_facts(rel, self.world, self.population, followers),
            }
        if typ == "death" and ev.get("person_id") is not None and ev.get("status", 0) > 0.6:
            p = self.population.get(ev.get("person_id"))
            if not p:
                return None
            return {
                "kind": "bio", "key": p.id,
                "sig": interpret_mod.person_signature(p),
                "system": interpret_mod.BIO_SYSTEM,
                "facts": interpret_mod.build_biography_facts(
                    p, self.world, self.society, self.life_chronicle(p),
                    self.family_tree(p.id) or {}),
            }
        if typ in ("culture", "faction_founded") and ev.get("culture_id") is not None:
            culture = self.society.cultures.get(ev.get("culture_id"))
            if not culture or not culture.alive:
                return None
            city = self.world.cities.get(culture.origin_city)
            return {
                "kind": "culture", "key": culture.id,
                "sig": interpret_mod.culture_signature(culture, self.world),
                "system": interpret_mod.CULTURE_SYSTEM,
                "facts": interpret_mod.build_culture_facts(
                    culture, self.world, city, culture.history[-8:]),
            }
        return None

    def _pending_obituary(self):
        """If a notable person died recently and hasn't been eulogized, return
        (city, 'obituary', context). Cheap scan of the materialized pool."""
        for p in self.population.people.values():
            if p.alive or getattr(p, "_eulogized", False):
                continue
            if p.death_tick is None or self.world.tick - p.death_tick > 200:
                continue
            notable = p.status > 0.55 or len(p.children) >= 2 or any(
                r.founder_id == p.id for r in self.society.religions.values())
            if not notable:
                p._eulogized = True
                continue
            p._eulogized = True
            city = self.world.cities.get(p.home_city)
            if city is None:
                continue
            ctx = (f" The departed: {p.name}, a {p.profession} of {city.name}, "
                   f"who died of {p.death_cause or 'age'}.")
            return city, "obituary", ctx
        return None

    async def _governor_loop(self) -> None:
        # let the world breathe a moment before the first deliberation
        await asyncio.sleep(min(5.0, self.cfg.governor.tick_seconds))
        while self.running:
            if self.cfg.governor.enabled and self.speed > 0:
                try:
                    stats = self.snapshot_stats()
                    self.last_governor = await self.governor.deliberate(stats)
                except Exception:  # noqa: BLE001
                    log.exception("governor deliberation failed")
            await asyncio.sleep(self.cfg.governor.tick_seconds)

    # ---------------- stats ----------------
    def _cheap_stats(self) -> dict:
        """A subset cheap enough to compute every sim tick for the metrics."""
        return stats_mod.compute(self.world, self.history, self.metrics)

    def snapshot_stats(self) -> dict:
        self.last_stats = stats_mod.compute(self.world, self.history, self.metrics)
        return self.last_stats

    # ---------------- serialization for the dashboard ----------------
    def serialize_overview(self) -> dict:
        s = self.snapshot_stats()
        return {"type": "overview", "stats": s, "speed": self.speed,
                "paused": self.speed <= 0,
                "presentation": self.serialize_presentation()}

    def serialize_presentation(self) -> dict:
        """Cosmetic render state the client applies: quality preset, texture pack,
        and per-frame budgets. Persisted into saves, broadcast on every overview."""
        return {"graphics_preset": self.graphics_preset,
                "texture_pack": self.texture_pack,
                "budgets": dict(self.render_budgets),
                "restart": dict(self.restart_meta)}

    def serialize_terrain(self, downsample: int = 2) -> dict:
        """Heightmap + biome grid for the 3D viewer (downsampled for bandwidth)."""
        w = self.world
        elev = w.elevation[::downsample, ::downsample]
        biome = w.biome[::downsample, ::downsample]
        water = w.water[::downsample, ::downsample]
        rainfall = w.rainfall[::downsample, ::downsample]
        minerals = w.minerals[::downsample, ::downsample]
        food = w.food[::downsample, ::downsample]
        return {
            "type": "terrain",
            "h": int(elev.shape[0]), "w": int(elev.shape[1]),
            "sea_level": float(w.params.sea_level),
            "elevation": np.round(elev, 3).astype(float).flatten().tolist(),
            "biome": biome.astype(int).flatten().tolist(),
            "water": np.round(water, 3).astype(float).flatten().tolist(),
            "rainfall": np.round(rainfall, 3).astype(float).flatten().tolist(),
            "minerals": np.round(minerals, 3).astype(float).flatten().tolist(),
            "food": np.round(food, 3).astype(float).flatten().tolist(),
        }

    def serialize_cities(self) -> dict:
        """Cities (rich), civilizations, and trade routes. Medium-rate channel.
        All positions normalized 0..1 so the client is resolution-independent."""
        sw, sh = self.world.width, self.world.height
        live = [c for c in self.world.cities.values() if c.alive]
        faction_pressure = {}
        for f in self.society.factions.values():
            if f.alive and f.seat_city is not None:
                faction_pressure[f.seat_city] = max(
                    faction_pressure.get(f.seat_city, 0.0), f.influence)
        cities = [{
            "id": c.id, "name": c.name, "civ": c.civ_id,
            "x": c.pos[1] / sw, "y": c.pos[0] / sh,
            "pop": int(c.population), "tier": c.tier,
            "radius": c.influence_radius / sw,        # fraction of world width
            "infra": round(c.infrastructure, 2), "culture_score": round(c.culture, 1),
            "growth": round(c.growth_rate, 4), "specialty": c.specialty,
            "famine": c.famine > 0, "plague": c.plague > 0,
            "wealth": round(c.wealth, 1), "unrest": round(c.unrest, 2),
            "economic_health": round(getattr(c, "economic_health", 1.0), 2),
            "demand_pressure": round(getattr(c, "demand_pressure", 0.0), 3),
            "trade_dependency": round(getattr(c, "trade_dependency", 0.0), 3),
            "famine_risk": round(getattr(c, "famine_risk", 0.0), 3),
            "war_readiness": round(getattr(c, "war_readiness", 0.0), 3),
            "civic_stability": round(getattr(c, "civic_stability", 1.0), 3),
            "heritage": round(getattr(c, "heritage", 0.0), 3),
            "trauma": round(getattr(c, "trauma", 0.0), 3),
            "damage": round(getattr(c, "damage", 0.0), 2),
            "stocks": {k: round(v, 1) for k, v in getattr(c, "stocks", {}).items()},
            "prices": {k: round(v, 2) for k, v in getattr(c, "prices", {}).items()},
            "resources": {
                "production": {k: round(v, 3) for k, v in getattr(c, "resource_production", {}).items()},
                "consumption": {k: round(v, 3) for k, v in getattr(c, "resource_consumption", {}).items()},
                "shortages": {k: round(v, 3) for k, v in getattr(c, "shortages", {}).items()},
                "surplus": {k: round(v, 3) for k, v in getattr(c, "surplus", {}).items()},
            },
            "demography": self._city_social_payload(c),
            "buildings": getattr(c, "buildings", {}),
            "religion": (lambda rs: rs[0].id if rs[0] else None)(
                self.society.religion_of_city(c.id)),
            "religion_share": round(self.society.religion_of_city(c.id)[1], 2),
            "culture_id": (lambda cs: cs[0].id if cs[0] else None)(
                self.society.culture_of_city(c.id)),
            "culture_share": round(self.society.culture_of_city(c.id)[1], 2),
            "faction_pressure": round(faction_pressure.get(c.id, 0.0), 2),
            "geo": self._city_geo(c),
            "economy": round(min(1.0, (c.wealth / 80) * 0.45
                                 + (c.infrastructure / 10) * 0.35
                                 + max(0, c.growth_rate) * 6
                                 + getattr(c, "economic_health", 1.0) * 0.2), 2),
        } for c in live]
        civs = [{
            "id": c.id, "name": c.name, "pop": int(c.population_of(self.world)),
            "ncities": len(c.cities(self.world)), "tech": round(c.tech, 2),
            "tech_domains": {k: round(v, 3) for k, v in getattr(c, "tech_domains", {}).items()},
            "tech_milestones": getattr(c, "tech_milestones", {}),
            # national identity — drives renderer colour and reads as a real character
            "people": getattr(c, "people", "Folk"),
            "color": getattr(c, "color", "#9b8cff"),
            "ideology": getattr(c, "ideology", "Tribal"),
            "stance": getattr(c, "diplomatic_stance", "neutral"),
            "traits": getattr(c, "cultural_traits", []),
            "desires": getattr(c, "preferred_desires", []),
            "biases": {"economic": round(getattr(c, "economic_bias", 0.5), 2),
                       "military": round(getattr(c, "military_bias", 0.5), 2),
                       "religious": round(getattr(c, "religious_bias", 0.5), 2),
                       "exploration": round(getattr(c, "exploration_bias", 0.5), 2)},
            "ideology_axes": {k: round(v, 2) for k, v in getattr(c, "ideology_axes", {}).items()},
            "status": getattr(c, "status", "stable"),
            "capital_id": getattr(c, "capital_city_id", None),
            "parent_civ_id": getattr(c, "parent_civ_id", None),
        } for c in self.world.civilizations.values() if c.alive]
        # trade routes: a road network — each city links to its 2 nearest
        # same-civ neighbours (deduped). Reads as roads, not a clutter of lines.
        routes, seen = [], set()
        for civ in self.world.civilizations.values():
            if not civ.alive:
                continue
            cl = civ.cities(self.world)
            for a in cl:
                nbrs = sorted(
                    (b for b in cl if b.id != a.id),
                    key=lambda b: abs(a.pos[0]-b.pos[0]) + abs(a.pos[1]-b.pos[1]))[:2]
                for b in nbrs:
                    if abs(a.pos[0]-b.pos[0]) + abs(a.pos[1]-b.pos[1]) > 50:
                        continue
                    key = (min(a.id, b.id), max(a.id, b.id))
                    if key in seen:
                        continue
                    seen.add(key)
                    importance = self._route_importance(a, b)
                    routes.append([a.pos[1]/sw, a.pos[0]/sh,
                                   b.pos[1]/sw, b.pos[0]/sh, civ.id,
                                   importance["score"], importance["kind"]])
        return {"type": "cities", "cities": cities, "civs": civs, "routes": routes}

    def _route_importance(self, a, b) -> dict:
        trade = min(1.0, (a.wealth + b.wealth) / 140
                    + (getattr(a, "trade_dependency", 0.0)
                       + getattr(b, "trade_dependency", 0.0)) * 0.2)
        migration = min(1.0, getattr(a, "migration_pressure", 0.0)
                        + getattr(b, "migration_pressure", 0.0))
        war = min(1.0, (1.0 - getattr(a, "civic_stability", 1.0))
                  + (1.0 - getattr(b, "civic_stability", 1.0))
                  + getattr(a, "war_readiness", 0.0) * 0.2
                  + getattr(b, "war_readiness", 0.0) * 0.2)
        kind = "trade"
        score = trade
        if migration > score:
            kind, score = "migration", migration
        if war > score and war > 0.45:
            kind, score = "military", war
        return {"kind": kind, "score": round(max(0.08, min(1.0, score)), 3)}

    def _city_social_payload(self, c) -> dict:
        city_mod._ensure_demographic_fields(c)
        return {
            "age_groups": {k: round(v, 3) for k, v in getattr(c, "demographics", {}).items()},
            "class_mix": {k: round(v, 3) for k, v in getattr(c, "class_mix", {}).items()},
            "professions": {k: round(v, 3) for k, v in getattr(c, "professions", {}).items()},
            "education": round(getattr(c, "education", 0.0), 3),
            "urbanization": round(getattr(c, "urbanization", 0.0), 3),
            "fertility_rate": round(getattr(c, "fertility_rate", 0.0), 5),
            "mortality_rate": round(getattr(c, "mortality_rate", 0.0), 5),
            "migration_pressure": round(getattr(c, "migration_pressure", 0.0), 3),
            "heritage": round(getattr(c, "heritage", 0.0), 3),
            "trauma": round(getattr(c, "trauma", 0.0), 3),
        }

    def _city_geo(self, c) -> dict:
        y, x = c.pos
        r = max(2, int(c.influence_radius))
        y0, y1 = max(0, y - r), min(self.world.height, y + r + 1)
        x0, x1 = max(0, x - r), min(self.world.width, x + r + 1)
        reg = (slice(y0, y1), slice(x0, x1))
        ocean = int(world_mod.BIOME["ocean"])
        mountain = int(world_mod.BIOME["mountain"])
        return {
            "coastal": bool((self.world.biome[reg] == ocean).any()),
            "mountain": float((self.world.biome[reg] == mountain).mean()),
            "fertility": round(float(self.world.food[reg].mean()), 2),
            "minerals": round(float(self.world.minerals[reg].mean()), 2),
            "river": bool(float(self.world.water[reg].max()) > 0.2),
        }

    def serialize_live(self) -> dict:
        """Units + world-space markers. The high-rate channel that makes the world
        move; the client interpolates unit positions between snapshots."""
        sw, sh = self.world.width, self.world.height
        units = [{"id": u.id, "k": u.code, "c": u.civ_id,
                  "x": u.pos[1] / sw, "y": u.pos[0] / sh}
                 for u in self.world.units.values()]
        markers = [{"kind": m["kind"], "x": m["x"] / sw, "y": m["y"] / sh,
                    "age": self.world.tick - m["born"], "ttl": m["ttl"],
                    "label": m["label"]} for m in self.world.markers]
        return {"type": "live", "t": self.world.tick,
                "units": units, "markers": markers}

    def serialize_wildlife(self) -> dict:
        """Species clouds — the ecology layer, shown under the Life overlay."""
        sw, sh = self.world.width, self.world.height
        return {"type": "wildlife", "species": [{
            "id": s.id, "name": s.name, "diet": s.diet, "pop": int(s.population),
            "x": s.pos[1] / sw, "y": s.pos[0] / sh,
        } for s in self.world.species.values() if s.alive]}

    def serialize_governor(self) -> dict:
        return {"type": "governor", **self.last_governor,
                "philosophy": self.memory.philosophy,
                "goal": self.memory.current_goal,
                "goal_reason": self.memory.goal_reason,
                "recent_decisions": self.memory.recent_decisions(6),
                "species_ai": self.world.species_brain.status(),
                "society_mind": self.serialize_mind(),
                "perf": {"sim_tick_ms": round(getattr(self, "sim_tick_ms", 0.0), 2),
                         "speed": self.speed, "tick": self.world.tick},
                "persistence": {"last_save": self.last_save,
                                "slots": len(self.save_store.list_slots())},
                "pool": {"people": len(self.population.people),
                         "focused": len(self.population.focus_cities)},
                "society": {
                    "religions": sum(1 for r in self.society.religions.values() if r.alive),
                    "cultures": sum(1 for c in self.society.cultures.values() if c.alive),
                    "factions": sum(1 for f in self.society.factions.values() if f.alive),
                    "chronicle": len(self.society.chronicle.entries)},
                "background_llm": {
                    "queued": self._llm_jobs.qsize(),
                    "deduped": len(self._llm_job_sigs),
                    "recently_chronicled": sum(
                        1 for r in self._llm_recent
                        if self.world.tick - r.get("tick", 0) <= 600),
                    "recent": self._llm_recent[-6:],
                },
                "params": self.world.params.as_dict()}

    def serialize_mind(self) -> dict:
        """Live status of the Society Intelligence Stack for the dashboard Mind panel."""
        if self.society_mind is None:
            return {"enabled": False}
        st = self.society_mind.status()
        # the observable "takeover": who is actually driving materialized citizens now
        mix = {"student": 0, "teacher": 0, "utility": 0}
        for p in self.population.people.values():
            if p.alive:
                mix[p.mind_source] = mix.get(p.mind_source, 0) + 1
        st["population_mix"] = mix
        st["spatial"] = self.spatial_debug()
        if self.teacher is not None:
            st["teacher"] = {**self.teacher.status(),
                             "model": self.cfg.mind.teacher_model,
                             "online": getattr(self._teacher_llm, "online", None)}
        st["arbiter"] = self.llm_arbiter.status()
        # checkpoint status for the dashboard (the student's weights are saved to this
        # slot every ~400 train steps and on shutdown).
        try:
            slot = self.cfg.mind.weights_slot
            wpath = self.save_store.weights_path(slot)
            st["checkpoint"] = {"slot": slot, "exists": Path(wpath).exists()}
        except Exception:  # noqa: BLE001
            st["checkpoint"] = {"slot": getattr(self.cfg.mind, "weights_slot", ""),
                                "exists": False}
        st["enabled"] = True
        return st

    def tune_mind(self, **knobs) -> dict:
        """Apply safe, live-tunable Society-Mind knobs from the God console.

        Only scalar caps that are thread-safe to change mid-run are accepted here:
        `autonomy_ratio` (ceiling on the student's population share) and
        `active_embodied_citizens` (how many citizens the student may drive per tick).
        Changing `student_size` needs a net rebuild, so it stays config-only."""
        m = self.society_mind
        if m is None:
            return {"ok": False, "message": "society mind is off"}
        applied: dict = {}
        if knobs.get("autonomy_ratio") is not None:
            v = max(0.0, min(1.0, float(knobs["autonomy_ratio"])))
            m.autonomy_ratio = v
            m.curriculum.autonomy_ratio = v
            self.cfg.mind.autonomy_ratio = v
            applied["autonomy_ratio"] = v
        if knobs.get("active_embodied_citizens") is not None:
            v = max(0, int(knobs["active_embodied_citizens"]))
            m.active_embodied_citizens = v
            self.cfg.mind.active_embodied_citizens = v
            applied["active_embodied_citizens"] = v
        return {"ok": bool(applied), "applied": applied,
                "student_size": getattr(m, "student_size", "tiny"),
                "autonomy_ratio": m.autonomy_ratio,
                "active_embodied_citizens": m.active_embodied_citizens}

    def rebuild_mind_model(self, *, size: str | None = None, hidden: int | None = None,
                           layers: int | None = None) -> dict:
        """Resize the liquid student net live from the dashboard.

        Unlike `tune_mind`'s scalar caps, the net's parameter shapes change, so the
        student is rebuilt and the previously trained weights are discarded (the corpus
        survives, so it retrains quickly). The chosen size is also written back onto the
        live config so a subsequent checkpoint/save reflects it."""
        m = self.society_mind
        if m is None:
            return {"ok": False, "message": "society mind is off"}
        res = m.rebuild_student(size=size, hidden=hidden, layers=layers)
        if res.get("ok"):
            # persist the choice onto the live config (size wins; raw dims override)
            self.cfg.mind.student_size = res["student_size"]
            self.cfg.mind.hidden = hidden
            self.cfg.mind.layers = layers
            self._mind_train_warned = False    # let a post-rebuild error surface once
        return res

    def spatial_debug(self) -> dict:
        counters = dict(getattr(self.population, "spatial_counters", {}))
        positioned = int(counters.get("positioned", 0))
        moving = int(counters.get("moving", 0))
        paths = int(counters.get("paths_requested", 0))
        path_failed = int(counters.get("path_failed", 0))
        avg_len = counters.get("path_length_sum", 0) / max(1, paths)
        actions: dict[str, int] = {}
        target_kinds: dict[str, int] = {}
        samples = []
        for p in self.population.people.values():
            if not p.alive:
                continue
            actions[p.last_action or "idle"] = actions.get(p.last_action or "idle", 0) + 1
            action = getattr(p, "current_action", {}) or {}
            tk = action.get("target_kind", "none")
            target_kinds[tk] = target_kinds.get(tk, 0) + 1
            if len(samples) < 8 and action:
                samples.append({"id": p.id, "name": p.name, "action": p.last_action,
                                "target": tk, "moving": bool(getattr(p, "moving", False)),
                                "path_len": len(getattr(p, "path", []) or [])})
        return {
            "positioned": positioned,
            "moving": moving,
            "population_embodiment_pct": round(positioned / max(1, len(self.population.people)) * 100, 1),
            "action_distribution": dict(sorted(actions.items(), key=lambda kv: -kv[1])[:12]),
            "target_distribution": dict(sorted(target_kinds.items(), key=lambda kv: -kv[1])[:12]),
            "avg_path_length": round(float(avg_len), 2),
            "failed_path_count": path_failed,
            "paths_requested": paths,
            "spatial_replay_samples": int(counters.get("spatial_replay_samples", 0)),
            "movement_events": int(counters.get("movement_events", 0)),
            "sampled_agents": samples,
            "feature_count": len(spatial_mod.SPATIAL_FEATURES),
        }

    def serialize_society(self) -> dict:
        """Religions and factions for the dashboard 'follow' browsers."""
        w = self.world
        religions = [{
            "id": r.id, "name": r.name, "founder": r.founder_name,
            "holy_city": r.holy_city_name, "cities": len(r.cities),
            "followers": r.follower_estimate(w), "tenet": r.tenets[0] if r.tenets else "",
            "schism": r.schism_parent is not None,
        } for r in self.society.religions.values() if r.alive]
        factions = [{
            "id": f.id, "name": f.name, "kind": f.kind, "goal": f.goal,
            "founder": f.founder_name, "seat": f.seat_city_name,
            "members": len(f.member_ids), "influence": round(f.influence, 2),
        } for f in self.society.factions.values() if f.alive]
        cultures = [{
            "id": c.id, "name": c.name, "origin": c.origin_city_name,
            "cities": len(c.cities), "value": c.values[0] if c.values else "",
            "architecture": c.architecture,
        } for c in self.society.cultures.values() if c.alive]
        return {"type": "society", "religions": religions, "factions": factions,
                "cultures": cultures}

    def serialize_chronicle(self) -> dict:
        return {"type": "chronicle", "entries": self.society.chronicle.recent(60)}

    def serialize_flavor(self, city_id: int | None = None) -> dict:
        if city_id is not None:
            return {"type": "flavor", "city_id": city_id,
                    "pieces": self.flavor.for_city(city_id, 10)}
        return {"type": "flavor", "pieces": self.flavor.recent(40)}

    def serialize_memory(self) -> dict:
        return {"type": "memory", "myths": self.memory.recent_myths(20),
                "philosophy": self.memory.philosophy,
                "goals_history": self.memory.goals_history[-10:],
                "observer": {
                    "persona": self.observer.persona,
                    "influence": round(self.observer.influence, 3),
                    "reputation": round(self.observer.reputation, 3),
                    "recent": self.observer.interventions[-8:],
                }}

    def serialize_metrics(self) -> dict:
        return {"type": "metrics", "series": self.metrics.export()}

    # ---- persistence ----
    def list_saves(self) -> dict:
        return {"slots": self.save_store.list_slots(),
                "autosave_slot": self.cfg.persistence.autosave_slot,
                "last_save": self.last_save}

    def save_world(self, slot: str = "manual", manual: bool = True) -> dict:
        slot = self._clean_slot(slot)
        weights_path = self.save_store.weights_path(slot)
        self.world.species_brain.save_weights(weights_path)
        # the society student holds threading.Locks (unpicklable): checkpoint its
        # weights separately and keep it detached for the whole pickle below.
        mind = getattr(self.world, "society_mind", None)
        if mind is not None:
            try:
                mind.save(self.save_store.weights_path(self.cfg.mind.weights_slot))
            except Exception:  # noqa: BLE001
                log.exception("society student checkpoint failed")
            delattr(self.world, "society_mind")
        try:
            state = self._state_for_save()
            summary = self._save_summary(slot)
            saved = self.save_store.save(slot, state, summary, weights_path, manual)
        finally:
            if mind is not None:
                self.world.society_mind = mind
        self.last_save = saved
        return saved

    def load_world(self, slot: str) -> dict:
        slot = self._clean_slot(slot)
        state, summary, weights_path = self.save_store.load(slot)
        self._restore_state(state, weights_path)
        self.last_save = {"slot": slot, **summary}
        self.history.add({"tick": self.world.tick, "type": "event",
                          "title": f"Loaded save slot {slot}",
                          "detail": "The world resumed from disk."})
        return {"slot": slot, "loaded": True, **summary}

    # ---- restart / reset ----
    def current_gen_config(self) -> WorldGenConfig:
        """Snapshot the live world-generation config (for GET /api/world/config)."""
        presentation = {"graphics_preset": self.graphics_preset,
                        "texture_pack": self.texture_pack, **self.render_budgets}
        return WorldGenConfig.from_engine(self.cfg, self.world.params, presentation)

    def restart(self, gen: WorldGenConfig, *, keep_minds: bool = False,
                parent_seed: int | None = None) -> dict:
        """Rebuild the world from scratch with `gen`. Deterministic for a given seed.

        Minds are reset fresh by default; `keep_minds=True` carries the trained
        per-species policies (and society student) across the new world. Runs on the
        event loop's cooperative thread, so the paused sim loop never interleaves."""
        self.speed = 0.0
        old_brain = getattr(self.world, "species_brain", None)
        old_mind = getattr(self.world, "society_mind", None)
        prev_seed = self.cfg.world.seed

        self.cfg = gen.apply_to_config(self.cfg)
        self.world = world_mod.create_world(self.cfg, params=gen.to_params())
        self.history = History(self.cfg.telemetry.history_max_events)
        self.metrics = Metrics(self.cfg.telemetry.metrics_window)
        self.population = PopulationManager(self.cfg)
        self.society = Society()
        self.observer = ObserverState()

        self._apply_presentation(gen.presentation)
        self._reset_minds(keep_minds, old_brain, old_mind)
        self._repoint_governor()

        self.history.add({"tick": 0, "type": "event", "kind": "genesis",
                          "title": "The world began anew", "detail": self.cfg.world.name})
        self.history.extend(getattr(self.world, "genesis_events", []))
        self._reset_runtime_cursors()

        self.restart_count += 1
        self.restart_meta = {
            "count": self.restart_count, "seed": self.cfg.world.seed,
            "parent_seed": prev_seed if parent_seed is None else parent_seed,
            "kept_minds": bool(keep_minds), "reset_layers": []}
        self.speed = 1.0
        log.info("world restarted: seed=%s keep_minds=%s", self.cfg.world.seed, keep_minds)
        return {"restarted": True, "seed": self.cfg.world.seed,
                "keep_minds": bool(keep_minds), **self._save_summary("(live)")}

    def restart_random(self, gen: WorldGenConfig | None = None, *,
                       keep_minds: bool = False) -> dict:
        """Restart with a fresh random seed, keeping all other gen settings."""
        import dataclasses as _dc
        import random as _r
        base = gen or self.current_gen_config()
        new = _dc.replace(base, seed=_r.randint(0, 2_147_483_647))
        return self.restart(new, keep_minds=keep_minds, parent_seed=self.cfg.world.seed)

    def reset_layer(self, layer: str, *, gen: WorldGenConfig | None = None) -> dict:
        """Reset a single subsystem. Terrain underpins everything → full rebuild;
        the others re-seed in place (with a full-rebuild fallback on error)."""
        if layer not in LAYERS:
            raise ValueError(f"unknown layer {layer!r}; expected one of {list(LAYERS)}")
        if layer == "minds":
            self._reset_minds(False, None, getattr(self.world, "society_mind", None))
            self.restart_meta["reset_layers"] = ["minds"]
            return {"layer": "minds", "reset": True}
        if layer == "terrain_climate":
            res = self.restart(gen or self.current_gen_config(), keep_minds=True)
            self.restart_meta["reset_layers"] = ["terrain_climate"]
            return {"layer": "terrain_climate", "reset": True, **res}
        try:
            if layer == "civilization":
                self._reset_civilization_layer()
            else:  # cities_population
                self._reset_cities_population()
        except Exception:  # noqa: BLE001 — never leave the world half-reset
            log.exception("targeted reset of %s failed; rebuilding world", layer)
            self.restart(gen or self.current_gen_config(), keep_minds=True)
        self.restart_meta["reset_layers"] = [layer]
        return {"layer": layer, "reset": True}

    # -- restart helpers --
    def _apply_presentation(self, presentation: dict) -> None:
        self.graphics_preset = presentation.get("graphics_preset", self.graphics_preset)
        self.texture_pack = presentation.get("texture_pack", self.texture_pack)
        for k in self.render_budgets:
            if k in presentation:
                self.render_budgets[k] = presentation[k]

    def _reset_minds(self, keep_minds: bool, old_brain, old_mind) -> None:
        if keep_minds and old_brain is not None:
            self.world.species_brain = old_brain
        else:
            self.world.species_brain = SpeciesBrain()
        if self.society_mind is not None or old_mind is not None:
            if keep_minds and old_mind is not None:
                old_mind._society = self.society
                self.society_mind = old_mind
                self.world.society_mind = old_mind
            else:
                try:
                    self._init_society_mind(load_weights=False)
                except Exception:  # noqa: BLE001
                    log.exception("fresh society-mind init failed; reattaching old")
                    if old_mind is not None:
                        old_mind._society = self.society
                        self.world.society_mind = old_mind
                        self.society_mind = old_mind

    def _repoint_governor(self) -> None:
        self.governor.world = self.world
        self.governor.history = self.history
        self.governor.metrics = self.metrics
        self.governor.memory = self.memory

    def _reset_runtime_cursors(self) -> None:
        self._policy_events = []
        self._experience_cursor = 0
        self._last_policy_tick = 0
        self._last_autosave_tick = self.world.tick
        self._newspaper = {"tick": -10_000, "items": ""}
        self._llm_last_discovery_tick = -10_000
        self._llm_history_cursor = self._latest_history_id()

    def _reset_civilization_layer(self) -> None:
        """Keep terrain/climate/species; rebuild the whole political + social stack."""
        w = self.world
        w.civilizations.clear(); w.cities.clear(); w.units.clear()
        w.markers.clear()
        w._next_civ_id = 1; w._next_city_id = 1; w._next_unit_id = 1
        self.society = Society()
        self.population = PopulationManager(self.cfg)
        events = civ_mod.seed_initial(
            w, n=int(getattr(self.cfg.sim, "start_civilizations", 5)))
        self.history.extend(events)
        self._reset_runtime_cursors()

    def _reset_cities_population(self) -> None:
        """Keep terrain/species/civ identities; wipe cities, units, and people, then
        re-found one capital per surviving civ on good, well-spaced land."""
        w = self.world
        w.cities.clear(); w.units.clear()
        w._next_city_id = 1; w._next_unit_id = 1
        self.population = PopulationManager(self.cfg)
        civs = [c for c in w.civilizations.values() if getattr(c, "status", "alive") != "dead"]
        pop0 = max(60.0, float(self.cfg.sim.start_population) * 0.01 / max(1, len(civs)))
        for civ in civs:
            civ.city_ids = []
            site = self._find_capital_site(w)
            if site is not None:
                city = city_mod.found_city(w, civ, site[0], site[1], population=pop0)
                civ.capital_city_id = city.id
        self._reset_runtime_cursors()

    def _find_capital_site(self, w) -> tuple[int, int] | None:
        land = np.argwhere(w.land_mask)
        if len(land) == 0:
            return None
        rng = w.rng.stream("relocate")
        sample = land[rng.choice(len(land), size=min(len(land), 800), replace=False)]
        existing = [c.pos for c in w.cities.values()]
        best, best_s = None, -1.0
        for y, x in sample:
            y, x = int(y), int(x)
            if any(abs(cy - y) + abs(cx - x) < city_mod.MIN_CITY_SPACING
                   for cy, cx in existing):
                continue
            s = city_mod.site_suitability(w, y, x)
            if s > best_s:
                best_s, best = s, (y, x)
        return best

    def _maybe_autosave(self) -> None:
        if not self.cfg.persistence.enabled:
            return
        every = max(1, int(self.cfg.persistence.autosave_ticks))
        if self.world.tick - self._last_autosave_tick < every:
            return
        self._last_autosave_tick = self.world.tick
        try:
            self.save_world(self.cfg.persistence.autosave_slot, manual=False)
        except Exception:  # noqa: BLE001
            log.exception("autosave failed")

    def _load_autosave_if_present(self) -> None:
        slot = self.cfg.persistence.autosave_slot
        if not self.save_store.has_slot(slot):
            return
        try:
            self.load_world(slot)
            log.info("loaded autosave slot '%s' at tick %s", slot, self.world.tick)
        except Exception:  # noqa: BLE001
            log.exception("failed to load autosave; starting a fresh world")

    def _state_for_save(self) -> dict:
        brain = getattr(self.world, "species_brain", None)
        if brain is not None:
            delattr(self.world, "species_brain")
        try:
            return {
                "save_version": 2,
                "world": self.world,
                "history": self.history,
                "metrics": self.metrics,
                "memory": self.memory,
                "population": self.population,
                "society": self.society,
                "observer": self.observer,
                "speed": self.speed,
                "last_stats": self.last_stats,
                "last_governor": self.last_governor,
                # generation config + presentation + restart lineage (v2). The world
                # object already pickles civilizations + params; these capture the rest.
                "gen_config": self.current_gen_config().as_dict(),
                "graphics_preset": self.graphics_preset,
                "texture_pack": self.texture_pack,
                "render_budgets": dict(self.render_budgets),
                "restart_meta": dict(self.restart_meta),
            }
        finally:
            if brain is not None:
                self.world.species_brain = brain

    def _restore_state(self, state: dict, weights_path: Path | None) -> None:
        self.world = state["world"]
        self.history = state["history"]
        self.metrics = state["metrics"]
        self.memory = state["memory"]
        self.population = state["population"]
        self.society = state["society"]
        if not hasattr(self.society, "cultures"):
            self.society.cultures = {}
        self.observer = state.get("observer", ObserverState())
        # always boot at x1 so the world is followable on load, regardless of the
        # speed the previous session left running (the "too fast on boot" fix).
        self.speed = 1.0
        self.last_stats = state.get("last_stats", {})
        self.last_governor = state.get("last_governor", self.last_governor)
        # presentation + restart metadata (v2 saves); old saves fall back to defaults.
        self.graphics_preset = state.get("graphics_preset", self.graphics_preset)
        self.texture_pack = state.get("texture_pack", self.texture_pack)
        if isinstance(state.get("render_budgets"), dict):
            self.render_budgets.update(state["render_budgets"])
        self.restart_meta = state.get("restart_meta", self.restart_meta)
        gen = state.get("gen_config")
        if isinstance(gen, dict):       # realign cfg structural fields with the save
            try:
                self.cfg = WorldGenConfig.from_dict(gen).apply_to_config(self.cfg)
            except Exception:  # noqa: BLE001 — never fail a load over config realign
                log.exception("could not realign config from save")
        self.world.species_brain = SpeciesBrain()
        self.world.species_brain.load_weights(weights_path)
        # re-attach the live society mind (not pickled) to the freshly-loaded world
        if self.society_mind is not None:
            self.society_mind._society = self.society
            self.world.society_mind = self.society_mind
        self._repair_loaded_state()
        self.governor.world = self.world
        self.governor.history = self.history
        self.governor.metrics = self.metrics
        self.governor.memory = self.memory
        self._last_autosave_tick = self.world.tick
        self._policy_events = []
        self._experience_cursor = len(self.population.experience)
        self._last_policy_tick = self.world.tick
        self._llm_history_cursor = self._latest_history_id()

    def _repair_loaded_state(self) -> None:
        if not hasattr(self.population, "spatial_index"):
            self.population.spatial_index = spatial_mod.SpatialIndex()
        if not hasattr(self.population, "spatial_counters"):
            self.population.spatial_counters = {
                "positioned": 0, "moving": 0, "paths_requested": 0,
                "path_failed": 0, "path_length_sum": 0, "movement_events": 0,
                "spatial_replay_samples": 0,
            }
        for p in self.population.people.values():
            for name, default in (
                ("mood", 0.0), ("stress", 0.0), ("trust_observer", 0.0),
                ("reputation", 0.0), ("possessions", {}), ("secrets", []),
                ("rumors", []), ("ambitions", []), ("active_plans", []),
                ("home_building", ""), ("work_building", ""),
                ("current_tile", (0, 0)), ("position", (0.0, 0.0)),
                ("home_position", (0.0, 0.0)), ("work_position", (0.0, 0.0)),
                ("destination", None), ("path", []), ("path_index", 0),
                ("path_progress", 0.0), ("moving", False), ("perception_radius", 8),
                ("current_action", {}),
            ):
                if not hasattr(p, name):
                    setattr(p, name, default.copy() if isinstance(default, (dict, list)) else default)
            city = self.world.cities.get(p.home_city) if p.home_city else None
            if city and (getattr(p, "position", (0.0, 0.0)) == (0.0, 0.0)):
                spatial_mod.initialize_person_position(self.world, p, city)
        for c in self.world.cities.values():
            city_mod._ensure_economy_fields(c)
            city_mod._ensure_demographic_fields(c)
            for name, default in (
                ("stocks", {}), ("prices", {}), ("buildings", {}),
                ("building_entities", {}),
                ("economic_health", 1.0), ("damage", 0.0),
                ("last_crisis_tick", -9999),
            ):
                if not hasattr(c, name):
                    setattr(c, name, default.copy() if isinstance(default, dict) else default)
            if not getattr(c, "building_entities", None):
                try:
                    city_mod._sync_building_entities(self.world, c)
                except Exception:  # noqa: BLE001
                    log.exception("failed to repair buildings for city %s", c.id)
        for civ in self.world.civilizations.values():
            if not hasattr(civ, "tech_domains"):
                civ.tech_domains = {}
            if not hasattr(civ, "tech_milestones") or civ.tech_milestones is None:
                civ.tech_milestones = {}
        for u in self.world.units.values():
            if not hasattr(u, "cargo"):
                u.cargo = {}
        try:
            self.population._rebuild_spatial()
        except Exception:  # noqa: BLE001
            log.exception("failed to rebuild citizen spatial index")
        if not hasattr(self.society, "cultures"):
            self.society.cultures = {}
        if not hasattr(self.world, "historical_sites") or self.world.historical_sites is None:
            self.world.historical_sites = []

    def _save_summary(self, slot: str) -> dict:
        live_cities = [c for c in self.world.cities.values() if c.alive]
        return {
            "slot": slot,
            "tick": self.world.tick,
            "world_name": self.cfg.world.name,
            "seed": self.cfg.world.seed,
            "cities": len(live_cities),
            "civilizations": sum(1 for c in self.world.civilizations.values()
                                 if c.alive),
            "people": len(self.population.people),
            "religions": sum(1 for r in self.society.religions.values()
                             if r.alive),
            "factions": sum(1 for f in self.society.factions.values()
                            if f.alive),
            "chronicle": len(self.society.chronicle.entries),
            "save_version": 2,
            "graphics_preset": self.graphics_preset,
            "texture_pack": self.texture_pack,
            "restarts": self.restart_count,
        }

    # ---- policy training data ----
    def _collect_policy_events(self, events: list[dict]) -> None:
        for ev in events:
            sample = self._sample_from_event(ev)
            if sample:
                self._policy_events.append(sample)
        if len(self._policy_events) > 12000:
            self._policy_events = self._policy_events[-12000:]

    def _sample_from_event(self, ev: dict) -> dict | None:
        typ = ev.get("type")
        cid = ev.get("city_id")
        city = self.world.cities.get(cid) if cid else None
        if city is None and ev.get("civ_id"):
            civ = self.world.civilizations.get(ev.get("civ_id"))
            city = max(civ.cities(self.world), key=lambda c: c.population,
                       default=None) if civ else None
        if city is None and self.world.cities:
            city = max((c for c in self.world.cities.values() if c.alive),
                       key=lambda c: c.population, default=None)
        mapping = {
            "famine": ("migrate", -0.45),
            "war": ("feud", 0.7 if "captured" in ev.get("title", "").lower() else -0.25),
            "migration": ("migrate", 0.35),
            "settlement": ("venture", 0.65),
            "collapse": ("rest", -0.8),
            "religion_founded": ("worship", 0.75),
            "schism": ("worship", -0.15),
            "holy_war": ("feud", 0.35),
            "faction_founded": ("socialize", 0.55),
            "revolution": ("feud", 0.7),
            "economy": ("work", -0.35),
            "culture": ("study", 0.35),
            "observer": ("socialize", 0.25),
            "rumor": ("socialize", 0.2),
            "trade": ("work", 0.45),
            "birth": ("court", 0.5),
            "death": ("rest", -0.5),
            "social": ("socialize", 0.25),
        }
        if typ not in mapping or city is None:
            return None
        action, reward = mapping[typ]
        return self._city_policy_sample(city, action, reward, f"event:{typ}")

    def _collect_periodic_policy_samples(self) -> None:
        if self.world.tick % 24 != 0:
            return
        for city in [c for c in self.world.cities.values() if c.alive][:80]:
            growth_reward = max(-1.0, min(1.0, city.growth_rate * 18))
            action = "work"
            if city.specialty == "Trade Port" or city.wealth > 20:
                action = "socialize"
            elif city.famine > 0 or city.unrest > 0.45:
                action = "migrate"
            elif city.specialty == "Cultural Center":
                action = "study"
            self._policy_events.append(
                self._city_policy_sample(city, action, growth_reward, "city_growth"))
        for rel in self.society.religions.values():
            if not rel.alive or not rel.cities:
                continue
            cid = max(rel.cities, key=rel.cities.get)
            city = self.world.cities.get(cid)
            if city:
                reward = max(-0.5, min(1.0, rel.follower_estimate(self.world) / 30000))
                self._policy_events.append(
                    self._city_policy_sample(city, "worship", reward, "religion_spread"))
        for fac in self.society.factions.values():
            city = self.world.cities.get(fac.seat_city)
            if city and fac.alive:
                action = "feud" if fac.kind == "revolutionary" else "socialize"
                self._policy_events.append(
                    self._city_policy_sample(city, action, fac.influence, "faction_influence"))

    def _city_policy_sample(self, city, action: str, reward: float, kind: str) -> dict:
        civ = self.world.civilizations.get(city.civ_id)
        species_id = civ.origin_species_id if civ else 0
        person = next(iter(self.population.residents(city.id)), None)
        if person:
            features = self.population.features(person, city, self.world)
        else:
            features = self._city_features(city)
        return {"species_id": species_id or 0, "action": action,
                "reward": float(max(-1.0, min(1.0, reward))),
                "features": features, "kind": kind,
                "tick": self.world.tick, "city_id": city.id}

    def _city_features(self, city) -> list[float]:
        demand = max(1e-6, city.population * 0.0013)
        scarcity = max(0.0, min(1.0, 1.0 - city.food_production / demand))
        y, x = city.pos
        temp = float(self.world.temperature[y, x])
        pressure = max(abs(temp - 18) / 35, scarcity,
                       1.0 if any(u.kind == "army" and u.dest_city == city.id
                                  for u in self.world.units.values()) else 0.0)
        return [
            0.5, 0.55, 0.5, 0.5, 0.45,
            0.45, max(0.0, 1.0 - city.unrest), min(1.0, city.wealth / 40),
            min(1.0, city.infrastructure / 10),
            1.0 if city.famine > 0 else 0.0, 0.5, 0.5,
            city.unrest, 0.35, min(1.0, city.unrest + getattr(city, "damage", 0.0) + 0.2),
            0.4, 0.6 if city.specialty == "Trade Port" else 0.25,
            scarcity, min(1.0, city.population / 25000), city.unrest,
            min(1.0, city.wealth / 80),
            min(1.0, (city.culture + getattr(city, "stocks", {}).get("knowledge", 0.0) * 0.1) / 120),
            min(1.0, city.infrastructure / 10), min(1.0, pressure),
        ] + [0.0] * len(spatial_mod.SPATIAL_FEATURES)

    @staticmethod
    def _clean_slot(slot: str) -> str:
        slot = (slot or "manual").strip()[:48]
        return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in slot) or "manual"

    def _latest_history_id(self) -> int:
        recent = self.history.recent(1)
        return int(recent[0].get("id", 0)) if recent else 0

    # ---- inspectors ----
    def inspect_species(self, sid: int) -> dict | None:
        s = self.world.species.get(sid)
        if not s:
            return None
        return {"id": s.id, "name": s.name, "diet": s.diet,
                "population": int(s.population), "genome": s.genome,
                "age": self.world.tick - s.born_tick,
                "ancestor_id": s.ancestor_id, "alive": s.alive,
                "habitat": {"x": s.pos[1], "y": s.pos[0]},
                "history": s.history}

    def inspect_civ(self, cid: int) -> dict | None:
        c = self.world.civilizations.get(cid)
        if not c:
            return None
        cs = c.cities(self.world)
        sw, sh = self.world.width, self.world.height
        return {"id": c.id, "name": c.name,
                "population": int(c.population_of(self.world)),
                "territory": len(cs), "tech": round(c.tech, 2),
                "tech_domains": {k: round(v, 3)
                                 for k, v in getattr(c, "tech_domains", {}).items()},
                "age": self.world.tick - c.founded_tick,
                "relations": c.relations, "alive": c.alive,
                "cities": [{"id": s.id, "name": s.name, "x": s.pos[1]/sw,
                            "y": s.pos[0]/sh, "pop": int(s.population),
                            "tier": s.tier} for s in cs],
                "history": c.history}

    def inspect_city(self, cid: int) -> dict | None:
        c = self.world.cities.get(cid)
        if not c:
            return None
        civ = self.world.civilizations.get(c.civ_id)
        sw, sh = self.world.width, self.world.height
        return {"id": c.id, "name": c.name, "tier": c.tier,
                "civ": civ.name if civ else "?", "civ_id": c.civ_id,
                "population": int(c.population), "specialty": c.specialty,
                "growth_rate": round(c.growth_rate * 100, 2),    # %/tick
                "food_production": round(c.food_production, 2),
                "culture": round(c.culture, 1),
                "infrastructure": round(c.infrastructure, 2),
                "influence_radius": round(c.influence_radius, 1),
                "wealth": round(c.wealth, 1),
                "economic_health": round(getattr(c, "economic_health", 1.0), 2),
                "demand_pressure": round(getattr(c, "demand_pressure", 0.0), 3),
                "trade_dependency": round(getattr(c, "trade_dependency", 0.0), 3),
                "famine_risk": round(getattr(c, "famine_risk", 0.0), 3),
                "war_readiness": round(getattr(c, "war_readiness", 0.0), 3),
                "civic_stability": round(getattr(c, "civic_stability", 1.0), 3),
                "heritage": round(getattr(c, "heritage", 0.0), 3),
                "trauma": round(getattr(c, "trauma", 0.0), 3),
                "damage": round(getattr(c, "damage", 0.0), 2),
                "stocks": {k: round(v, 1) for k, v in getattr(c, "stocks", {}).items()},
                "prices": {k: round(v, 2) for k, v in getattr(c, "prices", {}).items()},
                "resources": {
                    "production": {k: round(v, 3) for k, v in getattr(c, "resource_production", {}).items()},
                    "consumption": {k: round(v, 3) for k, v in getattr(c, "resource_consumption", {}).items()},
                    "shortages": {k: round(v, 3) for k, v in getattr(c, "shortages", {}).items()},
                    "surplus": {k: round(v, 3) for k, v in getattr(c, "surplus", {}).items()},
                },
                "demography": self._city_social_payload(c),
                "buildings": getattr(c, "buildings", {}),
                "building_entities": [{
                    "id": b.id, "kind": b.kind, "district": b.district,
                    "condition": round(b.condition, 2), "wealth": round(b.wealth, 2),
                    "workers": len(b.workers), "owner_id": b.owner_id,
                    "abandoned": b.abandoned,
                    "inventory": b.inventory, "production": b.production,
                } for b in list(getattr(c, "building_entities", {}).values())[:120]],
                "age": self.world.tick - c.founded_tick,
                "famine": c.famine > 0, "plague": c.plague > 0,
                "unrest": round(c.unrest, 2), "alive": c.alive,
                "x": c.pos[1]/sw, "y": c.pos[0]/sh,
                "chronicle": self.city_chronicle(c),
                "history": c.history}

    def city_chronicle(self, c) -> list[str]:
        """A city's history, generated deterministically from simulation truth:
        its founding, the recorded events that named it, and its present condition.
        No invention — every line is drawn from real state."""
        w = self.world
        civ = w.civilizations.get(c.civ_id)
        age = w.tick - c.founded_tick
        lines = [f"Founded {age} ticks ago" + (f" as a settlement of the {civ.name}." if civ else ".")]
        # real recorded events that mention this city, oldest first
        named = [e for e in self.history.filter(limit=4000)
                 if e.get("city_id") == c.id or c.name in (e.get("title", "") + e.get("detail", ""))]
        for e in sorted(named, key=lambda e: e["tick"])[:8]:
            lines.append(f"~{e['tick']}: {e.get('title','')}")
        # present condition, from current state
        cond = []
        if c.famine > 0: cond.append("gripped by famine")
        if c.plague > 0: cond.append("ravaged by plague")
        if c.unrest > 0.5: cond.append("simmering with unrest")
        rel, share = self.society.religion_of_city(c.id)
        if rel and share > 0.4: cond.append(f"largely faithful to the {rel.name}")
        if c.growth_rate > 0.01: cond.append("growing")
        elif c.growth_rate < -0.01: cond.append("in decline")
        lines.append("Today, " + (", ".join(cond) if cond else "at peace") +
                     f"; home to {int(c.population)} souls.")
        return lines

    def discoveries(self) -> dict:
        """World records — the surprising superlatives that invite exploration. Each
        is computed from live simulation state and carries a focus target so the
        Atlas can fly the camera to it. Nothing here is invented."""
        w = self.world
        out: list[dict] = []

        def rec(key, title, subject, detail, focus_kind, focus_id, value):
            out.append({"key": key, "title": title, "subject": subject,
                        "detail": detail, "focus": {"kind": focus_kind, "id": focus_id},
                        "value": value})

        live_cities = [c for c in w.cities.values() if c.alive]
        live_civs = [c for c in w.civilizations.values() if c.alive]
        people = [p for p in self.population.people.values() if p.alive]
        religions = [r for r in self.society.religions.values() if r.alive]
        factions = [f for f in self.society.factions.values() if f.alive]

        if live_cities:
            big = max(live_cities, key=lambda c: c.population)
            rec("largest_city", "Largest City", big.name,
                f"{int(big.population)} souls — a {big.tier}.", "city", big.id, int(big.population))
            rich = max(live_cities, key=lambda c: getattr(c, "wealth", 0))
            rw = round(float(getattr(rich, "wealth", 0)), 1)
            rec("richest_city", "Richest City", rich.name,
                f"Wealth {rw} — a {rich.specialty}.", "city", rich.id, rw)
            old = min(live_cities, key=lambda c: c.founded_tick)
            rec("oldest_city", "Oldest City", old.name,
                f"Founded {w.tick - old.founded_tick} ticks ago.", "city", old.id,
                w.tick - old.founded_tick)
            starving = [c for c in live_cities if c.famine > 0]
            if starving:
                worst = max(starving, key=lambda c: c.population)
                rec("famine_hotspot", "Famine Hotspot", worst.name,
                    f"{int(worst.population)} souls go hungry.", "city", worst.id,
                    int(worst.population))
        if people:
            elder = max(people, key=lambda p: p.age)
            rec("oldest_citizen", "Oldest Living Citizen", elder.name,
                f"Aged {elder.age}, a {elder.profession} of {elder.birthplace}.",
                "person", elder.id, elder.age)
            tycoon = max(people, key=lambda p: p.wealth)
            rec("richest_citizen", "Richest Citizen", tycoon.name,
                f"A {tycoon.profession} of great means.", "person", tycoon.id,
                round(tycoon.wealth, 1))
            patriarch = max(people, key=lambda p: len(p.children))
            if len(patriarch.children):
                rec("largest_family", "Largest Family", patriarch.name,
                    f"{len(patriarch.children)} children.", "person", patriarch.id,
                    len(patriarch.children))
            malcontent = max(people, key=lambda p: p.grievance)
            if malcontent.grievance > 0.4:
                rec("most_aggrieved", "Most Aggrieved Citizen", malcontent.name,
                    f"Grievance {round(malcontent.grievance*100)}% — a spark for revolt.",
                    "person", malcontent.id, round(malcontent.grievance, 2))
        if religions:
            faith = max(religions, key=lambda r: r.follower_estimate(w))
            rec("largest_religion", "Largest Religion", faith.name,
                f"~{faith.follower_estimate(w)} faithful, from {faith.holy_city_name}.",
                "religion", faith.id, faith.follower_estimate(w))
        if factions:
            power = max(factions, key=lambda f: f.influence)
            rec("most_influential_faction", "Most Influential Faction", power.name,
                f"A {power.kind.replace('_',' ')} of {power.seat_city_name}.",
                "faction", power.id, round(power.influence, 2))
        if live_civs:
            empire = max(live_civs, key=lambda c: c.population_of(w))
            rec("greatest_power", "Greatest Power", empire.name,
                f"{len(empire.cities(w))} cities, {int(empire.population_of(w))} subjects.",
                "civ", empire.id, int(empire.population_of(w)))
        # deadliest war / largest migration from recorded history
        wars = self.history.filter(type="war", limit=400)
        if wars:
            rec("recent_war", "Bloodiest Recent War", wars[0].get("title", "A war"),
                wars[0].get("detail", ""), "city", wars[0].get("city_id"), len(wars))
        migs = self.history.filter(type="migration", limit=400)
        if migs:
            rec("great_migration", "Great Migration", migs[0].get("title", "A migration"),
                migs[0].get("detail", ""), "city", migs[0].get("city_id"),
                len(migs))
        return {"discoveries": out, "tick": w.tick}

    # ---------------- the individual layer ----------------
    def focus_city(self, cid: int) -> dict:
        """Materialize a city's residents (LOD) and return the roster."""
        self.population.focus(self.world, cid)
        return self.city_people(cid)

    def city_people(self, cid: int) -> dict:
        people = sorted(self.population.residents(cid),
                        key=lambda p: -p.status)
        city = self.world.cities.get(cid)
        by_class: dict[str, int] = {}
        by_profession: dict[str, int] = {}
        active: dict[str, int] = {}
        for p in people:
            by_class[p.social_class] = by_class.get(p.social_class, 0) + 1
            by_profession[p.profession] = by_profession.get(p.profession, 0) + 1
            active[p.last_action or "idle"] = active.get(p.last_action or "idle", 0) + 1
        return {"city_id": cid, "people": [{
            "id": p.id, "name": p.name, "age": p.age, "sex": p.sex,
            "profession": p.profession, "social_class": p.social_class,
            "status": round(p.status, 2), "goal": p.dominant_goal(),
            "doing": p.last_action,
            "health": round(p.health, 2), "wealth": round(p.wealth, 2),
            "grievance": round(p.grievance, 2),
            "religion_id": p.religion_id, "faction_count": len(p.faction_ids),
        } for p in people],
                "summary": {
                    "city_name": city.name if city else "unknown",
                    "statistical_population": int(city.population) if city else 0,
                    "materialized": len(people),
                    "by_class": by_class,
                    "by_profession": dict(sorted(
                        by_profession.items(), key=lambda kv: -kv[1])[:8]),
                    "active": dict(sorted(active.items(), key=lambda kv: -kv[1])[:8]),
                }}

    def people_directory(self, city_id: int | None = None, q: str = "",
                         alive: bool | None = True, limit: int = 60, offset: int = 0,
                         focus: bool = False) -> dict:
        """Search the materialized persona pool with pagination.

        The statistical population can be huge; the directory lists the real Person
        objects currently materialized by LOD. Selecting/focusing a city promotes its
        residents into that pool on demand. Always paginated (limit+offset) so the UI
        never has to render a giant list.
        """
        if city_id is not None and focus:
            self.population.focus(self.world, city_id)
        ql = q.strip().lower()
        if city_id is not None:
            people = list(self.population.residents(city_id, include_dead=alive is not True))
        else:
            people = list(self.population.people.values())
        if alive is True:
            people = [p for p in people if p.alive]
        elif alive is False:
            people = [p for p in people if not p.alive]
        if ql:
            def matches(p) -> bool:
                city = self.world.cities.get(p.home_city) if p.home_city else None
                hay = " ".join([
                    p.name, p.profession, p.social_class, p.species,
                    p.dominant_goal(), city.name if city else "",
                    p.last_action or "",
                ]).lower()
                return ql in hay
            people = [p for p in people if matches(p)]
        people = sorted(people, key=lambda p: (p.home_city or -1, -p.status, p.name))
        total = len(people)
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        page = people[offset:offset + limit]
        return {"people": [self._person_list_item(p) for p in page],
                "count": total, "limit": limit, "offset": offset,
                "has_more": offset + limit < total,
                "pool": {"people": len(self.population.people),
                         "focused_cities": len(self.population.focus_cities)}}

    def cities_directory(self, q: str = "", limit: int = 60, offset: int = 0) -> dict:
        """Paginated, searchable list of living cities (lightweight summaries)."""
        ql = q.strip().lower()
        cities = [c for c in self.world.cities.values() if c.alive]
        if ql:
            cities = [c for c in cities
                      if ql in c.name.lower() or ql in c.specialty.lower()
                      or ql in c.tier.lower()]
        cities.sort(key=lambda c: -c.population)
        total = len(cities)
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        page = cities[offset:offset + limit]
        return {"cities": [{
            "id": c.id, "name": c.name, "pop": int(c.population), "tier": c.tier,
            "specialty": c.specialty, "civ_id": c.civ_id,
            "famine": c.famine > 0, "plague": c.plague > 0,
            "unrest": round(c.unrest, 2),
            "focused": c.id in self.population.focus_cities,
            "materialized": len(self.population.residents(c.id)),
        } for c in page], "count": total, "limit": limit, "offset": offset,
            "has_more": offset + limit < total}

    def buildings_directory(self, city_id: int, district_id: str = "",
                            q: str = "", limit: int = 60, offset: int = 0) -> dict:
        """Paginated buildings within a city (optionally one district)."""
        city = self.world.cities.get(city_id)
        if not city:
            return {"error": "not found"}
        ql = q.strip().lower()
        items = [b for b in getattr(city, "building_entities", {}).values()]
        if district_id:
            items = [b for b in items if b.district == district_id]
        if ql:
            items = [b for b in items if ql in b.kind.lower()
                     or ql in getattr(b, "district", "").lower()]
        items.sort(key=lambda b: (b.abandoned, b.district, b.kind))
        total = len(items)
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        page = items[offset:offset + limit]
        return {"city_id": city_id, "city": city.name,
                "buildings": [{
                    "id": b.id, "kind": b.kind, "district": b.district,
                    "abandoned": bool(b.abandoned),
                    "condition": round(getattr(b, "condition", 1.0), 2),
                    "wealth": round(getattr(b, "wealth", 0.0), 2),
                } for b in page],
                "count": total, "limit": limit, "offset": offset,
                "has_more": offset + limit < total}

    def inspect_building(self, building_id: str) -> dict | None:
        from .render.projection import entity_payload
        bid = building_id.split(":", 1)[1] if building_id.startswith("building:") \
            else building_id
        data = entity_payload(self, f"building:{bid}")
        return data["data"] if data else None

    def _person_list_item(self, p) -> dict:
        city = self.world.cities.get(p.home_city) if p.home_city else None
        return {
            "id": p.id, "name": p.name, "age": p.age, "sex": p.sex,
            "profession": p.profession, "social_class": p.social_class,
            "status": round(p.status, 2), "goal": p.dominant_goal(),
            "doing": p.last_action, "health": round(p.health, 2),
            "wealth": round(p.wealth, 2), "grievance": round(p.grievance, 2),
            "city_id": p.home_city, "city": city.name if city else "wandering",
            "religion_id": p.religion_id, "faction_count": len(p.faction_ids),
        }

    def inspect_person(self, pid: int) -> dict | None:
        p = self.population.get(pid)
        if not p:
            return None
        rels = []
        for r in sorted(p.relationships.values(), key=lambda r: -abs(r.strength))[:10]:
            o = self.population.get(r.other_id)
            rels.append({"id": r.other_id, "name": o.name if o else "(forgotten)",
                         "kind": r.kind, "strength": round(r.strength, 2),
                         "note": r.note})
        city = self.world.cities.get(p.home_city) if p.home_city else None
        spatial_obs = spatial_mod.compact_observation(
            self.world, self.population, p, city, getattr(self.population, "spatial_index", None)
        ) if p.alive else {}
        return {
            "id": p.id, "name": p.name, "summary": p.summary(),
            "archive": self.deceased_archive(p) if not p.alive else None,
            "age": p.age, "sex": p.sex, "species": p.species,
            "profession": p.profession, "education": p.education,
            "social_class": p.social_class, "birthplace": p.birthplace,
            "home_city": city.name if city else "wandering",
            "home_city_id": p.home_city,
            "alive": p.alive, "death_cause": p.death_cause,
            "wealth": round(p.wealth, 2), "health": round(p.health, 2),
            "status": round(p.status, 2), "doing": p.last_action,
            "mood": round(p.mood, 2), "stress": round(p.stress, 2),
            # inner life written by the Society Intelligence Stack (who's thinking, how)
            "emotion": p.emotion, "intent": p.intent,
            "last_dialogue": p.last_dialogue, "mind_source": p.mind_source,
            "trust_observer": round(p.trust_observer, 2),
            "possessions": p.possessions, "secrets": p.secrets[-6:],
            "rumors": p.rumors[-8:], "ambitions": p.ambitions[-6:],
            "active_plans": p.active_plans[-8:],
            "home_building": p.home_building, "work_building": p.work_building,
            "spatial": spatial_obs,
            "current_action": getattr(p, "current_action", {}),
            "moving": bool(getattr(p, "moving", False)),
            "path_length": len(getattr(p, "path", []) or []),
            "personality": p.personality, "goals": p.goals,
            "dominant_goal": p.dominant_goal(),
            "skills": {k: round(v, 2) for k, v in p.skills.items() if v > 0.15},
            "beliefs": p.beliefs, "fears": p.fears, "preferences": p.preferences,
            "ideology": p.ideology, "grievance": round(p.grievance, 2),
            # individuating colour (objective 2): what makes this person singular
            "quirk": getattr(p, "quirk", ""), "speech_style": getattr(p, "speech_style", ""),
            "life_goal": getattr(p, "life_goal", ""),
            "personal_problem": getattr(p, "personal_problem", ""),
            "past_event": getattr(p, "past_event", ""),
            "civ_loyalty": round(getattr(p, "civ_loyalty", 0.5), 2),
            "class_tension": round(getattr(p, "class_tension", 0.0), 2),
            "local_identity": getattr(p, "local_identity", ""),
            "civ": (self.world.civilizations[p.civ_id].name
                    if p.civ_id in self.world.civilizations else None),
            "religion": (self.society.religions[p.religion_id].name
                         if p.religion_id in self.society.religions else None),
            "religion_id": p.religion_id,
            "factions": [{"id": fid, "name": self.society.factions[fid].name}
                         for fid in p.faction_ids if fid in self.society.factions],
            "relationships": rels, "milestones": p.milestones[-12:],
            "memories": [{"text": m.text, "kind": m.kind,
                          "salience": round(m.salience, 2), "valence": m.valence}
                         for m in p.memory.top(12)],
            "life_chronicle": self.life_chronicle(p),
            "activity": self._activity_phrase(p),
        }

    # ---- Phase 10: a person's life story, assembled from real memory + state ----
    _CHRON_ICON = {"birth": "👶", "marriage": "💍", "death": "⚰", "conflict": "⚔",
                   "achievement": "⭐", "migration": "🧭", "faith": "🙏",
                   "civic": "🏛", "conversation": "💬"}

    def person_live(self, pid: int) -> dict | None:
        """Compact live snapshot for the citizen-follow HUD (Phase 9). Cheap enough
        to poll every couple of seconds."""
        p = self.population.get(pid)
        if not p:
            return None
        city = self.world.cities.get(p.home_city) if p.home_city else None
        rel = self.society.religions.get(p.religion_id) if p.religion_id else None
        kin = []
        if p.partner_id and self.population.get(p.partner_id):
            kin.append({"id": p.partner_id, "rel": "spouse",
                        "name": self.population.get(p.partner_id).name})
        for cid in p.children[:4]:
            ch = self.population.get(cid)
            if ch:
                kin.append({"id": cid, "rel": "child", "name": ch.name})
        sched = schedule_mod.schedule(p, self.world) if p.alive else None
        action = getattr(p, "current_action", {}) or {}
        return {
            "id": p.id, "name": p.name, "alive": p.alive, "age": p.age, "sex": p.sex,
            "profession": p.profession, "social_class": p.social_class,
            "species": p.species, "city": city.name if city else "wandering",
            "city_id": p.home_city, "religion": rel.name if rel else None,
            "faction_count": len(p.faction_ids),
            "activity": (sched["phrase"] if sched else self._activity_phrase(p)),
            "hour": sched["hour"] if sched else None,
            "time_of_day": sched["time_of_day"] if sched else None,
            "next_activity": sched["next_phrase"] if sched else None,
            "next_hour": sched["next_hour"] if sched else None,
            "destination": sched["destination"] if sched else None,
            "action_target": action,
            "position": [round(float(getattr(p, "position", (0.0, 0.0))[1]), 2),
                         round(float(getattr(p, "position", (0.0, 0.0))[0]), 2)],
            "moving": bool(getattr(p, "moving", False)),
            "path_length": len(getattr(p, "path", []) or []),
            "why": sched["why"] if sched else f"died of {p.death_cause or 'age'}",
            "season": season_mod.name(self.world.tick),
            "mood": round(p.mood, 2), "health": round(p.health, 2),
            "wealth": round(float(p.wealth), 1), "kin": kin,
            "home_building": p.home_building, "work_building": p.work_building,
        }

    def family_tree(self, pid: int) -> dict | None:
        """Phase 2 — an explorable family: parents, spouse, siblings, children, with
        living/dead status and ids to jump to. Lazy — only pool members are resolved."""
        p = self.population.get(pid)
        if not p:
            return None

        def node(qid):
            q = self.population.get(qid)
            if not q:
                return {"id": qid, "name": "(lost to history)", "alive": False,
                        "profession": "", "known": False}
            return {"id": q.id, "name": q.name, "alive": q.alive,
                    "profession": q.profession, "age": q.age, "known": True,
                    "dynasty": q.name.split(" ")[-1]}

        siblings = []
        for par in p.parents:
            par_obj = self.population.get(par)
            if par_obj:
                for sib in par_obj.children:
                    if sib != p.id and sib not in [s["id"] for s in siblings]:
                        siblings.append(node(sib))
        return {
            "id": p.id, "name": p.name, "dynasty": p.name.split(" ")[-1],
            "self": node(p.id),
            "parents": [node(x) for x in p.parents],
            "spouse": node(p.partner_id) if p.partner_id else None,
            "siblings": siblings,
            "children": [node(x) for x in p.children],
            "family_influence": round(min(1.0, (p.status + 0.1 * len(p.children)
                                          + min(1.0, p.wealth / 30)) / 2), 2),
        }

    async def _narrate(self, kind: str, key, sig: str, system: str, facts: str) -> dict:
        """Shared cached-LLM narration: return cached prose if the entity is unchanged,
        else generate (async, off the sim loop), cache it, and return. Grounded — the
        `facts` are pure simulation truth assembled by the caller."""
        cached = self.interp.get(kind, key, sig)
        if cached:
            return {"text": cached, "cached": True}
        text = (await self.governor.llm.complete(
            system, facts, format_json=False, consumer="narration",
            cache_key=f"narr:{kind}:{key}:{sig}", tick=self.world.tick,
            meta={"city": str(key)}) or "").strip()
        if text and not text.startswith("(…"):
            self.interp.put(kind, key, sig, text)
        return {"text": text, "cached": False}

    async def biography(self, pid: int) -> dict:
        """Phase 2 — an LLM-written biography grounded in the person's real life."""
        p = self.population.get(pid)
        if not p:
            return {"error": "not found"}
        facts = interpret_mod.build_biography_facts(
            p, self.world, self.society, self.life_chronicle(p),
            self.family_tree(pid) or {})
        r = await self._narrate("bio", pid, interpret_mod.person_signature(p),
                                interpret_mod.BIO_SYSTEM, facts)
        return {"id": pid, "name": p.name, "biography": r["text"], "cached": r["cached"]}

    async def city_history(self, cid: int) -> dict:
        """Phase 5 — an LLM city history, grounded in the city's real founding,
        events, faith, and economy. Cached; regenerates only as the city changes."""
        c = self.world.cities.get(cid)
        if not c or not c.alive:
            return {"error": "not found"}
        civ = self.world.civilizations.get(c.civ_id)
        rel, share = self.society.religion_of_city(c.id)
        chron = self.city_chronicle(c)
        facts = interpret_mod.build_city_facts(c, self.world, civ, rel, share, chron)
        r = await self._narrate("city", cid, interpret_mod.city_signature(c, len(chron)),
                                interpret_mod.CITY_SYSTEM, facts)
        return {"id": cid, "name": c.name, "history": r["text"], "cached": r["cached"]}

    async def religion_history(self, rid: int) -> dict:
        """Phase 6 — an LLM account of a faith, grounded in its founder, tenets,
        spread, and schism. Invents no doctrine beyond the real tenets."""
        rel = self.society.religions.get(rid)
        if not rel or not rel.alive:
            return {"error": "not found"}
        followers = rel.follower_estimate(self.world)
        facts = interpret_mod.build_religion_facts(rel, self.world, self.population, followers)
        r = await self._narrate("relig", rid,
                                interpret_mod.religion_signature(rel, followers),
                                interpret_mod.RELIGION_SYSTEM, facts)
        return {"id": rid, "name": rel.name, "history": r["text"], "cached": r["cached"]}

    async def culture_history(self, cid: int) -> dict:
        """LLM account of a culture, grounded in its real values and spread."""
        culture = self.society.cultures.get(cid)
        if not culture or not culture.alive:
            return {"error": "not found"}
        city = self.world.cities.get(culture.origin_city)
        facts = interpret_mod.build_culture_facts(
            culture, self.world, city, culture.history[-8:])
        r = await self._narrate("culture", cid,
                                interpret_mod.culture_signature(culture, self.world),
                                interpret_mod.CULTURE_SYSTEM, facts)
        return {"id": cid, "name": culture.name, "history": r["text"], "cached": r["cached"]}

    async def discovery_narrative(self, key: str) -> dict:
        """LLM note for an existing discovery record; no generated discoveries."""
        found = None
        for d in self.discoveries().get("discoveries", []):
            if d.get("key") == key:
                found = d
                break
        if found is None:
            return {"error": "not found"}
        sig = interpret_mod.discovery_signature(found)
        facts = interpret_mod.build_discovery_facts(self.world, found)
        r = await self._narrate("discovery", key, sig,
                                interpret_mod.DISCOVERY_SYSTEM, facts)
        return {"key": key, "narrative": r["text"], "cached": r["cached"],
                "discovery": found}

    async def newspaper(self) -> dict:
        """Phase 6 — the Daily World Report: recent real events written up as news.
        Rate-limited by tick so it isn't regenerated constantly."""
        if self.world.tick - self._newspaper["tick"] < 120 and self._newspaper["items"]:
            return {"tick": self._newspaper["tick"], "items": self._newspaper["items"],
                    "cached": True}
        major = [e for e in self.history.recent(120)
                 if e.get("type") in ("war", "holy_war", "revolution", "civilization",
                                      "religion_founded", "schism", "migration",
                                      "famine", "collapse", "settlement")]
        if not major:
            return {"tick": self.world.tick, "items": "", "cached": False}
        facts = interpret_mod.build_newspaper_facts(
            self.world, major, season_mod.name(self.world.tick))
        text = await self.governor.llm.complete(
            interpret_mod.NEWS_SYSTEM, facts, format_json=False,
            consumer="news", tick=self.world.tick,
            cache_key=f"news:{self.world.tick // 100}")
        text = (text or "").strip()
        if text and not text.startswith("(…"):
            self._newspaper = {"tick": self.world.tick, "items": text}
        return {"tick": self.world.tick, "items": text, "cached": False}

    def life_chronicle(self, p) -> list[dict]:
        """A chronological life story drawn entirely from the person's own memories
        and recorded state — birth, marriages, children, migrations, faith, deeds,
        death. Nothing invented."""
        events: list[dict] = []
        events.append({"tick": p.born_tick, "icon": "👶",
                       "text": f"Born in {p.birthplace}" +
                       (f", into the {p.social_class} class." if p.social_class else ".")})
        # real episodic memories carry ticks and kinds → the spine of the timeline
        for m in sorted(p.memory.items, key=lambda m: m.tick):
            if m.kind == "conversation":
                continue            # interviews aren't life events
            events.append({"tick": m.tick, "icon": self._CHRON_ICON.get(m.kind, "•"),
                           "text": m.text})
        # structured facts the memory may not hold
        if p.religion_id in self.society.religions:
            events.append({"tick": p.born_tick, "icon": "⛪",
                           "text": f"Of the faith {self.society.religions[p.religion_id].name}."})
        for fid in p.faction_ids:
            f = self.society.factions.get(fid)
            if f:
                events.append({"tick": f.founded_tick, "icon": "⚔",
                               "text": f"Member of {f.name}."})
        if not p.alive and p.death_tick is not None:
            events.append({"tick": p.death_tick, "icon": "⚰",
                           "text": f"Died of {p.death_cause or 'age'}, aged {p.age}."})
        events.sort(key=lambda e: e["tick"])
        return events

    def _activity_phrase(self, p) -> str:
        """Human verb for what a citizen is doing right now (Phase 9 follow HUD)."""
        if not p.alive:
            return "at rest in death"
        a = (p.last_action or "").lower()
        city = self.world.cities.get(p.home_city) if p.home_city else None
        verbs = {
            "work": "working", "trade": "trading at the market",
            "socialize": "among friends", "court": "courting",
            "feud": "quarrelling with a rival", "migrate": "preparing to leave",
            "study": "at their studies", "worship": "at prayer",
            "rest": "resting at home", "venture": "venturing abroad",
        }
        base = verbs.get(a, "going about their day")
        if city and city.famine > 0:
            base += " amid famine"
        elif city and city.plague > 0:
            base += " as plague spreads"
        return base

    def inspect_religion(self, rid: int) -> dict | None:
        r = self.society.religions.get(rid)
        if not r:
            return None
        sw, sh = self.world.width, self.world.height
        cities = [{"id": cid, "name": self.world.cities[cid].name,
                   "x": self.world.cities[cid].pos[1]/sw,
                   "y": self.world.cities[cid].pos[0]/sh,
                   "share": round(share, 2)}
                  for cid, share in sorted(r.cities.items(), key=lambda kv: -kv[1])
                  if cid in self.world.cities and self.world.cities[cid].alive]
        parent = self.society.religions.get(r.schism_parent) if r.schism_parent else None
        return {"id": r.id, "name": r.name, "founder": r.founder_name,
                "founder_id": r.founder_id, "tenets": r.tenets,
                "holy_city": r.holy_city_name, "holy_city_id": r.holy_city,
                "followers": r.follower_estimate(self.world),
                "cities": cities, "schism_of": parent.name if parent else None,
                "age": self.world.tick - r.founded_tick, "history": r.history}

    def inspect_faction(self, fid: int) -> dict | None:
        f = self.society.factions.get(fid)
        if not f:
            return None
        members = []
        for mid in f.member_ids[:20]:
            p = self.population.get(mid)
            if p:
                members.append({"id": p.id, "name": p.name,
                                "profession": p.profession, "alive": p.alive})
        rel = self.society.religions.get(f.religion_id) if f.religion_id else None
        return {"id": f.id, "name": f.name, "kind": f.kind, "goal": f.goal,
                "founder": f.founder_name, "founder_id": f.founder_id,
                "seat": f.seat_city_name, "seat_id": f.seat_city,
                "influence": round(f.influence, 2), "members": members,
                "member_count": len(f.member_ids),
                "religion": rel.name if rel else None,
                "age": self.world.tick - f.founded_tick, "history": f.history}

    def deceased_archive(self, p) -> dict:
        """What remains of a dead person: biography, remembered quotes, legacy,
        descendants, and anything they founded. No live dialogue — the dead don't
        answer questions; they are read from the world's memory."""
        quotes = [m.text for m in sorted(p.memory.items, key=lambda m: -m.salience)[:6]] \
            if getattr(p.memory, "items", None) else []
        founded_religions = [{"id": r.id, "name": r.name}
                             for r in self.society.religions.values()
                             if r.founder_id == p.id]
        founded_factions = [{"id": f.id, "name": f.name}
                            for f in self.society.factions.values()
                            if f.founder_id == p.id]
        descendants = []
        for cid in p.children:
            child = self.population.get(cid)
            if child:
                descendants.append({"id": cid, "name": child.name,
                                    "alive": child.alive})
        return {
            "deceased": True,
            "death_cause": p.death_cause or "unknown",
            "death_tick": p.death_tick,
            "lifespan": (p.death_tick - p.born_tick) if p.death_tick else None,
            "biography": p.milestones,
            "quotes": quotes,
            "legacy": {
                "founded_religions": founded_religions,
                "founded_factions": founded_factions,
                "children": len(p.children),
                "descendants": descendants,
            },
        }

    async def interview_person(self, pid: int, question: str) -> dict:
        p = self.population.get(pid)
        if not p:
            return {"error": "not found"}
        if not p.alive:
            # the dead do not hold live conversation; surface the archive instead
            return {
                "id": pid, "name": p.name, "question": question,
                "deceased": True,
                "answer": (f"{p.name} died {('of ' + p.death_cause) if p.death_cause else 'long ago'}. "
                           f"The dead keep their silence — but their memory endures in "
                           f"the world's chronicle and the lives they touched."),
                "archive": self.deceased_archive(p),
            }
        answer = await interview_mod.interview(
            self.governor.llm, p, self.world, self.population, question)
        # the conversation itself becomes a (faint) memory
        p.remember(f"A traveller asked me: \"{question[:80]}\"", "conversation",
                   self.world.tick, 0.1)
        consequence = self._conversation_consequence(p, question, answer)
        return {"id": pid, "name": p.name, "question": question, "answer": answer,
                "consequence": consequence}

    def _conversation_consequence(self, p, question: str, answer: str) -> dict:
        q = question.lower()
        city = self.world.cities.get(p.home_city) if p.home_city else None
        effects: list[str] = []
        text = question.strip()
        plan = None
        if any(w in q for w in ("faith", "god", "doctrine", "prophet", "sacred")):
            doctrine = self._extract_doctrine(question)
            p.beliefs.append(doctrine)
            p.ideology["piety"] = min(1.0, p.ideology.get("piety", 0) + 0.18)
            plan = {"kind": "preach", "source": "observer", "doctrine": doctrine,
                    "progress": 0, "duration": 3}
            effects.append("seeded doctrine")
            if city and p.status + p.ideology.get("piety", 0) > 1.15:
                rel = self._found_observer_religion(p, doctrine, city)
                effects.append(f"founded {rel.name}")
        elif any(w in q for w in ("rebel", "revolt", "tyrant", "overthrow", "freedom")):
            p.grievance = min(1.0, p.grievance + 0.25)
            p.ideology["radicalism"] = min(1.0, p.ideology.get("radicalism", 0) + 0.2)
            plan = {"kind": "rebel", "source": "observer", "progress": 0, "duration": 4}
            effects.append("radicalized")
            if city and p.grievance + p.status > 1.0:
                fac = self._found_observer_faction(p, city, "revolutionary")
                effects.append(f"founded {fac.name}")
        elif any(w in q for w in ("guild", "trade", "profit", "merchant", "market")):
            p.ideology["mercantilism"] = min(1.0, p.ideology.get("mercantilism", 0) + 0.18)
            plan = {"kind": "trade", "source": "observer", "progress": 0, "duration": 2}
            effects.append("encouraged trade")
            if city and p.wealth + p.status * 20 > 12:
                fac = self._found_observer_faction(p, city, "merchant_league")
                effects.append(f"founded {fac.name}")
        elif any(w in q for w in ("leave", "migrate", "journey", "escape")):
            p.rootedness = max(0.0, p.rootedness - 0.22)
            plan = {"kind": "migrate", "source": "observer", "progress": 0, "duration": 3}
            effects.append("encouraged migration")
        elif any(w in q for w in ("join", "organize", "movement", "recruit")):
            plan = {"kind": "recruit", "source": "observer", "progress": 0, "duration": 3}
            effects.append("seeded recruitment")
        else:
            p.mood = max(-1.0, min(1.0, p.mood + 0.04))
            effects.append("remembered conversation")
        if plan:
            p.active_plans.append(plan)
            p.active_plans = p.active_plans[-8:]
        p.trust_observer = min(1.0, p.trust_observer + 0.06)
        p.rumors.append(f"The traveller said: {text[:120]}")
        p.rumors = p.rumors[-12:]
        p.remember(f"The traveller's words changed my mind: {text[:90]}",
                   "conversation", self.world.tick, 0.35)
        self.observer.record(self.world.tick, p.id, ", ".join(effects), text)
        if effects:
            self.history.add({"tick": self.world.tick, "type": "observer",
                              "title": f"Observer influenced {p.name}",
                              "detail": "; ".join(effects)})
        return {"effects": effects, "planned": plan["kind"] if plan else None,
                "observer_persona": self.observer.persona}

    def _extract_doctrine(self, question: str) -> str:
        q = question.strip().rstrip("?!.")
        if len(q) < 16:
            return "The hidden voice has chosen the humble."
        return q[:140]

    def _found_observer_religion(self, p, doctrine: str, city) -> Religion:
        rid = self.society.nid()
        name = f"Way of {p.name.split()[0]}"
        rel = Religion(id=rid, name=name, founder_id=p.id, founder_name=p.name,
                       tenets=[doctrine, "The spoken word may become law.",
                               "Memory is sacred."],
                       holy_city=city.id, holy_city_name=city.name,
                       civ_origin=p.civ_id, founded_tick=self.world.tick)
        rel.cities[city.id] = 0.35
        rel.history.append(f"Born from a conversation with the observer at tick {self.world.tick}.")
        self.society.religions[rid] = rel
        p.religion_id = rid
        p.milestones.append(f"Founded {name} after speaking with the traveller.")
        self.world.add_marker("religion", city.pos[0], city.pos[1], ttl=120, label=name)
        ev = {"tick": self.world.tick, "type": "religion_founded", "religion_id": rid,
              "civ_id": p.civ_id, "title": f"{p.name} founded {name}",
              "detail": f"A conversation hardened into doctrine: {doctrine}",
              "major": True}
        self.history.add(ev); self.society.pending_chronicle.append(ev)
        return rel

    def _found_observer_faction(self, p, city, kind: str) -> Faction:
        fid = self.society.nid()
        label = "Uprising" if kind == "revolutionary" else "League"
        name = f"{city.name} {label} of {p.name.split()[0]}"
        fac = Faction(id=fid, name=name, kind=kind, goal=FACTION_GOAL[kind],
                      founder_id=p.id, founder_name=p.name, seat_city=city.id,
                      seat_city_name=city.name, civ_id=p.civ_id,
                      founded_tick=self.world.tick, member_ids=[p.id],
                      religion_id=p.religion_id, influence=0.12)
        fac.history.append(f"Founded after the observer spoke with {p.name}.")
        self.society.factions[fid] = fac
        if fid not in p.faction_ids:
            p.faction_ids.append(fid)
        p.milestones.append(f"Founded {name} after speaking with the traveller.")
        ev = {"tick": self.world.tick, "type": "faction_founded", "faction_id": fid,
              "civ_id": p.civ_id, "title": f"{p.name} founded {name}",
              "detail": "A private conversation became an organization.",
              "major": kind == "revolutionary"}
        self.history.add(ev); self.society.pending_chronicle.append(ev)
        return fac

    # ---------------- controls + god actions ----------------
    def set_speed(self, speed: float) -> None:
        self.speed = max(0.0, min(self.cfg.sim.max_speed, float(speed)))

    def pause(self) -> None:
        self.speed = 0.0

    def god_action(self, op: str, **payload) -> dict:
        """Apply a player directive (God Console). Same safe path as the spirit."""
        d = Directive.parse({"op": op, "reason": "god console", **payload})
        if d is None:
            return {"ok": False, "message": f"invalid op {op}"}
        res = apply_directive(self.world, self.memory, d)
        if res.ok:
            self.history.add({"tick": self.world.tick, "type": "governor",
                              "title": f"You: {res.message}",
                              "detail": "via God Console"})
        return {"ok": res.ok, "message": res.message}
