"""WorldParams — the *only* surface the governor is allowed to write to.

Every field is a global pressure knob the deterministic sim reads each tick. Each
has a hard [min, max] clamp so the world-spirit can never push the world into an
invalid state, no matter what the LLM hallucinates.

Adding a new knob is a three-step contract:
  1. add the field + bound here,
  2. read it somewhere in sim/,
  3. mention it in governor/prompts.py so the spirit knows it exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass
class Bound:
    lo: float
    hi: float
    default: float
    desc: str

    def clamp(self, v: float) -> float:
        return max(self.lo, min(self.hi, v))


# The knob registry. Keep descriptions short — they are fed verbatim to the LLM.
BOUNDS: dict[str, Bound] = {
    "rainfall_multiplier":  Bound(0.1, 4.0, 1.0, "global rainfall scale"),
    "temperature_bias":     Bound(-25.0, 25.0, 0.0, "degrees C added everywhere"),
    "storm_intensity":      Bound(0.0, 4.0, 1.0, "frequency/severity of storms"),
    "sea_level":            Bound(-0.3, 0.3, 0.0, "ocean height offset (drowns/exposes land)"),
    "volcanic_activity":    Bound(0.0, 1.0, 0.0, "chance of eruptions reshaping terrain"),
    "tectonic_drift":       Bound(0.0, 1.0, 0.0, "rate of slow elevation change"),
    "resource_richness":    Bound(0.2, 3.0, 1.0, "mineral/energy abundance"),
    "plant_growth":         Bound(0.2, 3.0, 1.0, "vegetation/food regrowth rate"),
    "prey_fertility":       Bound(0.2, 3.0, 1.0, "herbivore/plant-eater birth rate"),
    "predator_fertility":   Bound(0.2, 3.0, 1.0, "predator birth rate"),
    "mutation_rate":        Bound(0.0, 0.5, 0.02, "per-generation chance of mutation"),
    "carrying_capacity":    Bound(0.3, 3.0, 1.0, "how much life the land supports"),
    "civ_expansion_drive":  Bound(0.0, 3.0, 1.0, "how aggressively civs settle new land"),
    "war_propensity":       Bound(0.0, 3.0, 1.0, "likelihood civs go to war"),
    "tech_progress":        Bound(0.0, 3.0, 1.0, "rate of civilization tech advance"),
}


@dataclass
class WorldParams:
    rainfall_multiplier: float = 1.0
    temperature_bias: float = 0.0
    storm_intensity: float = 1.0
    sea_level: float = 0.0
    volcanic_activity: float = 0.0
    tectonic_drift: float = 0.0
    resource_richness: float = 1.0
    plant_growth: float = 1.0
    prey_fertility: float = 1.0
    predator_fertility: float = 1.0
    mutation_rate: float = 0.02
    carrying_capacity: float = 1.0
    civ_expansion_drive: float = 1.0
    war_propensity: float = 1.0
    tech_progress: float = 1.0

    @classmethod
    def from_defaults(cls) -> "WorldParams":
        return cls(**{k: b.default for k, b in BOUNDS.items()})

    def set(self, key: str, value: float) -> float:
        """Set a knob to an absolute value, clamped. Returns the applied value."""
        if key not in BOUNDS:
            raise KeyError(f"unknown param {key!r}")
        v = BOUNDS[key].clamp(float(value))
        setattr(self, key, v)
        return v

    def adjust(self, key: str, delta_pct: float) -> float:
        """Nudge a knob by a percentage of its current value, clamped."""
        if key not in BOUNDS:
            raise KeyError(f"unknown param {key!r}")
        cur = getattr(self, key)
        return self.set(key, cur * (1.0 + delta_pct / 100.0))

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def keys(self) -> list[str]:
        return [f.name for f in fields(self)]
