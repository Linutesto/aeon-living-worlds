"""WorldGenConfig — the editable, validated bundle of world-generation variables.

This is the single source of truth for "what knobs can a player set before (re)starting
a world". It deliberately reuses the existing levers rather than inventing parallel ones:

  * **structural** ints (seed, map size, starting species/population/civilizations) feed
    `world.create_world`,
  * the **sim knobs** are exactly `sim/params.BOUNDS` — the same clamped registry the
    governor writes to — so a player-set `sea_level`/`war_propensity`/… has identical,
    deterministic effect to a governor directive,
  * **presentation** fields (graphics preset, texture pack, render budgets, density) are
    cosmetic: persisted and shipped to the renderer, never touching determinism.

Everything is bounds-checked here, so the API/UI layers can stay dumb and a malformed
request can never push the sim into an invalid state. Same seed + same config ⇒ identical
world (params are injected *before* genesis seeding, which reads them — see create_world).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from .params import BOUNDS, WorldParams

# Mirrors the client quality ladder in web/js/omega/QualityGovernor.js.
GRAPHICS_PRESETS = ["emergency", "mobile-low", "mobile-high", "desktop",
                    "ultra", "rtx-4090-ultra"]
# Mirrors the pack folders under web/assets/texturepacks/.
TEXTURE_PACKS = ["default-clean", "realistic-medieval", "snowy-ice-age",
                 "volcanic-ash", "lush-green", "desert-dry", "dark-fantasy",
                 "performance-low"]

# name -> (lo, hi, default, desc). These map onto cfg.world / cfg.sim.
STRUCT_FIELDS: dict[str, tuple[int, int, int, str]] = {
    "seed":                (0, 2_147_483_647, 1337, "world RNG seed (determinism)"),
    "width":               (64, 384, 192, "map width in tiles"),
    "height":              (64, 384, 192, "map height in tiles"),
    "start_species":       (1, 20, 6, "wildlife lineages seeded at genesis"),
    "start_population":    (100, 40_000, 4000, "total wildlife population at genesis"),
    "start_civilizations": (1, 12, 5, "distinct rival nations seeded at genesis"),
}

# name -> ("int"|"float", lo, hi, default, desc) or ("enum", [options], default, desc)
PRESENT_FIELDS: dict[str, tuple] = {
    "graphics_preset":  ("enum", GRAPHICS_PRESETS, "desktop", "render quality preset"),
    "texture_pack":     ("enum", TEXTURE_PACKS, "default-clean", "active texture pack"),
    "lod_distance":     ("float", 0.2, 4.0, 1.0, "LOD distance multiplier"),
    "max_buildings":    ("int", 500, 40_000, 18_000, "max buildings rendered"),
    "max_particles":    ("int", 0, 20_000, 6000, "max particles rendered"),
    "max_lights":       ("int", 0, 4000, 800, "max dynamic lights rendered"),
    "city_density":     ("float", 0.2, 2.0, 1.0, "relative city count"),
    "building_density": ("float", 0.2, 2.0, 1.0, "relative buildings per city"),
    "road_density":     ("float", 0.2, 2.0, 1.0, "relative road coverage"),
}

LAYERS = ("civilization", "terrain_climate", "cities_population", "minds")


def _clamp_int(v: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError) as e:
        raise ValueError(f"expected an integer, got {v!r}") from e


def _clamp_float(v: Any, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError) as e:
        raise ValueError(f"expected a number, got {v!r}") from e


@dataclass
class WorldGenConfig:
    seed: int = 1337
    width: int = 192
    height: int = 192
    name: str = "Aeon-Prime"
    start_species: int = 6
    start_population: int = 4000
    start_civilizations: int = 5
    params: dict[str, float] = field(default_factory=dict)        # BOUNDS knobs
    presentation: dict[str, Any] = field(default_factory=dict)    # cosmetic

    # ---------------------------------------------------------------- builders
    @classmethod
    def from_defaults(cls) -> "WorldGenConfig":
        return cls(
            params={k: b.default for k, b in BOUNDS.items()},
            presentation={k: (spec[2] if spec[0] == "enum" else spec[3])
                          for k, spec in PRESENT_FIELDS.items()},
        )

    @classmethod
    def from_engine(cls, cfg, params: WorldParams, presentation: dict | None = None
                    ) -> "WorldGenConfig":
        """Snapshot the live config + params into an editable bundle."""
        base = cls.from_defaults()
        return cls(
            seed=int(cfg.world.seed), width=int(cfg.world.width),
            height=int(cfg.world.height), name=str(cfg.world.name),
            start_species=int(cfg.sim.start_species),
            start_population=int(cfg.sim.start_population),
            start_civilizations=int(getattr(cfg.sim, "start_civilizations", 5)),
            params={k: float(getattr(params, k)) for k in BOUNDS},
            presentation={**base.presentation, **(presentation or {})},
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, base: "WorldGenConfig | None" = None
                  ) -> "WorldGenConfig":
        """Validate an untrusted dict into a config, merged onto `base` (or defaults).

        Raises ValueError on unknown keys or wrong types; numeric fields are clamped.
        """
        if not isinstance(raw, dict):
            raise ValueError("config must be an object")
        cfg = dataclasses.replace(base) if base else cls.from_defaults()
        cfg.params = dict(cfg.params)
        cfg.presentation = dict(cfg.presentation)

        allowed = set(STRUCT_FIELDS) | {"name", "params", "presentation"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")

        for k, (lo, hi, _d, _desc) in STRUCT_FIELDS.items():
            if k in raw:
                setattr(cfg, k, _clamp_int(raw[k], lo, hi))
        if "name" in raw:
            cfg.name = str(raw["name"])[:64] or "Aeon-Prime"

        for k, v in (raw.get("params") or {}).items():
            if k not in BOUNDS:
                raise ValueError(f"unknown param: {k!r}")
            cfg.params[k] = BOUNDS[k].clamp(float(v))

        for k, v in (raw.get("presentation") or {}).items():
            if k not in PRESENT_FIELDS:
                raise ValueError(f"unknown presentation key: {k!r}")
            spec = PRESENT_FIELDS[k]
            if spec[0] == "enum":
                if v not in spec[1]:
                    raise ValueError(f"{k} must be one of {spec[1]}")
                cfg.presentation[k] = v
            elif spec[0] == "int":
                cfg.presentation[k] = _clamp_int(v, spec[1], spec[2])
            else:
                cfg.presentation[k] = _clamp_float(v, spec[1], spec[2])
        return cfg

    # ---------------------------------------------------------------- apply
    def apply_to_config(self, cfg):
        """Return a copy of `cfg` with world/sim overridden by this gen config."""
        world = dataclasses.replace(cfg.world, seed=self.seed, width=self.width,
                                    height=self.height, name=self.name)
        sim = dataclasses.replace(cfg.sim, start_species=self.start_species,
                                  start_population=self.start_population,
                                  start_civilizations=self.start_civilizations)
        return dataclasses.replace(cfg, world=world, sim=sim)

    def to_params(self) -> WorldParams:
        p = WorldParams.from_defaults()
        for k, v in self.params.items():
            if k in BOUNDS:
                p.set(k, v)
        return p

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed, "width": self.width, "height": self.height,
            "name": self.name, "start_species": self.start_species,
            "start_population": self.start_population,
            "start_civilizations": self.start_civilizations,
            "params": dict(self.params), "presentation": dict(self.presentation),
        }

    # ---------------------------------------------------------------- schema
    @staticmethod
    def schema() -> dict[str, Any]:
        """Self-describing field list for the UI (types/bounds/defaults/options)."""
        structural = [{"key": k, "type": "int", "lo": lo, "hi": hi,
                       "default": d, "desc": desc}
                      for k, (lo, hi, d, desc) in STRUCT_FIELDS.items()]
        structural.append({"key": "name", "type": "str", "default": "Aeon-Prime",
                           "desc": "world name"})
        params = [{"key": k, "type": "float", "lo": b.lo, "hi": b.hi,
                   "default": b.default, "desc": b.desc} for k, b in BOUNDS.items()]
        presentation = []
        for k, spec in PRESENT_FIELDS.items():
            if spec[0] == "enum":
                presentation.append({"key": k, "type": "enum", "options": spec[1],
                                     "default": spec[2], "desc": spec[3]})
            else:
                presentation.append({"key": k, "type": spec[0], "lo": spec[1],
                                     "hi": spec[2], "default": spec[3], "desc": spec[4]})
        return {"structural": structural, "params": params,
                "presentation": presentation, "layers": list(LAYERS)}
