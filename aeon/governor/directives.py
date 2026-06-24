"""Directive schema, validation, and safe application.

A directive is the *only* thing the world-spirit (or a god-console action) may emit.
Every directive is validated against a whitelist of operations and clamped before it
touches the sim. A malformed or out-of-range directive is rejected and logged, never
applied — the LLM cannot break the world.

Operations
----------
  set_param     {key, value}            absolute knob value (clamped)
  adjust_param  {key, value}            percentage nudge of current value
  trigger_event {kind, [duration]}      fire a god-mode/natural event
  spawn_species {diet, [near]}          introduce a new species archetype
  set_goal      {value}                 record a long-term objective (memory)
  add_myth      {title, value}          record a myth/legend (memory)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..sim.params import BOUNDS
from ..sim import events as sim_events
from ..sim import species as sim_species

log = logging.getLogger("aeon.directives")

VALID_OPS = {"set_param", "adjust_param", "trigger_event",
             "spawn_species", "set_goal", "add_myth"}


@dataclass
class Directive:
    op: str
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @classmethod
    def parse(cls, raw: dict) -> "Directive | None":
        op = str(raw.get("op", "")).strip()
        if op not in VALID_OPS:
            log.debug("rejected directive: bad op %r", op)
            return None
        payload = {k: v for k, v in raw.items() if k not in ("op", "reason")}
        return cls(op=op, payload=payload, reason=str(raw.get("reason", "")))


@dataclass
class ApplyResult:
    directive: Directive
    ok: bool
    message: str


def apply(world, memory, directive: Directive) -> ApplyResult:
    """Validate + apply one directive to the world. Pure dispatch; each branch
    is responsible for its own bounds checking."""
    try:
        fn = _DISPATCH[directive.op]
    except KeyError:
        return ApplyResult(directive, False, f"unknown op {directive.op}")
    try:
        msg = fn(world, memory, directive.payload, directive.reason)
        return ApplyResult(directive, True, msg)
    except Exception as e:  # noqa: BLE001
        log.warning("directive %s failed: %s", directive.op, e)
        return ApplyResult(directive, False, str(e))


def _set_param(world, memory, p, reason) -> str:
    key = p["key"]
    if key not in BOUNDS:
        raise ValueError(f"unknown param {key}")
    applied = world.params.set(key, float(p["value"]))
    return f"set {key} = {applied:.3g}"


def _adjust_param(world, memory, p, reason) -> str:
    key = p["key"]
    if key not in BOUNDS:
        raise ValueError(f"unknown param {key}")
    applied = world.params.adjust(key, float(p["value"]))
    return f"adjusted {key} by {float(p['value']):+.0f}% -> {applied:.3g}"


def _trigger_event(world, memory, p, reason) -> str:
    kind = p["kind"]
    ev = sim_events.apply(world, kind, source="governor",
                          **{k: v for k, v in p.items() if k != "kind"})
    memory.record_event(ev)
    return f"triggered {kind}"


def _spawn_species(world, memory, p, reason) -> str:
    rng = world.rng.stream("species")
    diet = p.get("diet", sim_species.HERBIVORE)
    land = world.land_mask
    import numpy as np
    ys, xs = np.where(land)
    i = int(rng.integers(0, len(ys)))
    sp = sim_species.spawn(
        world, diet=diet, pos=(float(ys[i]), float(xs[i])),
        population=float(p.get("population", 200)),
        genome=sim_species._random_genome(rng), name=p.get("name"),
    )
    return f"spawned species {sp.name} ({diet})"


def _set_goal(world, memory, p, reason) -> str:
    memory.set_goal(str(p["value"]), reason)
    return f"goal set: {p['value']}"


def _add_myth(world, memory, p, reason) -> str:
    memory.add_myth(str(p.get("title", "Untitled")), str(p["value"]))
    return "myth recorded"


_DISPATCH = {
    "set_param": _set_param,
    "adjust_param": _adjust_param,
    "trigger_event": _trigger_event,
    "spawn_species": _spawn_species,
    "set_goal": _set_goal,
    "add_myth": _add_myth,
}
