"""Load and validate config.yaml into typed config objects."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


@dataclass
class WorldConfig:
    seed: int = 1337
    width: int = 192
    height: int = 192
    name: str = "Aeon-Prime"


@dataclass
class SimConfig:
    tick_seconds: float = 0.2          # legacy; loop now uses base_tps below
    base_tps: float = 3.0              # sim ticks per second at speed x1 (followable)
    loop_hz: float = 20.0              # how often the sim loop wakes (smoothness)
    max_steps_per_wake: int = 40       # cap ticks per wake so x100 can't stall the loop
    max_speed: int = 100
    start_species: int = 6
    start_population: int = 4000
    start_civilizations: int = 5       # distinct rival nations seeded at genesis


@dataclass
class GovernorConfig:
    enabled: bool = True
    backend: str = "ollama"
    model: str = "jaahas/qwen3.5-uncensored:2b"
    base_url: str = "http://localhost:11434"
    flavor_interval: float = 14.0      # seconds between async world-flavor pieces
    tick_seconds: float = 0.0
    tick_seconds = None  # deprecated: synced to engine.world.tick loop
    temperature: float = 0.9
    max_tokens: int = 800
    timeout_seconds: float = 45.0
    think: bool = False     # set true only for reasoning models you *want* to think
    event_base_chance: float = 0.05
    keep_alive: str = "10m"  # keep the spirit's model warm in VRAM between calls
    llm_max_concurrent: int = 1     # one GPU can't truly parallelize generation
    llm_budget_per_min: int = 60_000  # rolling token budget; low-priority work yields


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    broadcast_hz: float = 12.0
    terrain_every: int = 120


@dataclass
class TelemetryConfig:
    history_max_events: int = 5000
    metrics_window: int = 2000


@dataclass
class PersistenceConfig:
    enabled: bool = True
    autosave_slot: str = "autosave"
    autosave_ticks: int = 250
    autosave_on_boot: bool = True


@dataclass
class MindConfig:
    """The Society Intelligence Stack (aeon/mind/): batch-teacher → liquid student."""
    enabled: bool = True
    teacher_model: str = "vaultbox/qwen3.5-uncensored:27b"
    teacher_max_tokens: int = 2560        # room for ~60 citizens' JSON, not more
    teacher_num_ctx: int = 6144           # cap KV cache so the 27B fits 24GB VRAM
    teacher_timeout: float = 180.0
    keep_alive: str = "30m"               # keep the 27B resident between cohorts
    cohort_interval: float = 12.0         # seconds between teacher cohort calls
    # Keep the cohort small enough that the 27B + its KV cache FIT in 24GB VRAM. A
    # 200-citizen prompt + 3k output spilled the model to CPU and made each call crawl;
    # ~60 keeps it fully GPU-resident and fast. The system runs many small cohorts.
    cohort_size: int = 60                 # max citizens compressed into one call
    cohort_min: int = 6
    train_interval: float = 0.4           # seconds between background train steps
    batch_size: int = 256
    min_samples: int = 40                 # behavior samples needed before training
    swap_every: int = 5                   # publish weights every N train steps
    val_fraction: float = 0.12            # held-out share for validation metrics + drift
    val_every: int = 25                   # validate every N train steps
    awr_beta: float = 1.0                 # advantage-weighted regression temperature
    balance_actions: bool = False         # class-balanced replay sampling (vs prioritized)
    min_for_split: int = 256              # corpus size before a val split is held out
    warmup_steps: int = 20                # train steps before the student drives anyone
    # Student size. Named sizes (tiny~0.7M / small~3M / medium~10M / large~15M) resolve
    # to (hidden, layers) in mind/liquid.py:MODEL_SIZES. Leave hidden/layers None to use
    # the size; set them to pin raw dims for an experiment (they override the size).
    student_size: str = "tiny"
    hidden: int | None = None             # CfC width — None ⇒ derived from student_size
    layers: int | None = None
    # Tiered cognition: most citizens run the cheap utility model; only a bounded set of
    # embodied citizens run the liquid student each life-tick (one batched GPU forward).
    # The teacher (27B) supervises crisis cohorts. These knobs are surfaced in the UI.
    active_embodied_citizens: int = 240   # max citizens the student may drive per tick
    teacher_sampling_rate: float = 1.0    # 0..1 — fraction of training batch drawn from
    #                                       teacher labels at cold start (decays as the
    #                                       student earns trust; see trainer._teacher_ratio)
    autonomy_ratio: float = 0.7           # ceiling on the student's population share once
    #                                       fully trusted (1.0 = it may drive everyone)
    lr: float = 2e-3
    confidence_gate: float = 0.45         # agreement at which the student drives all
    min_teacher_agreement: float = 0.74   # below this, student cannot drive population
    population_takeover_threshold: float = 0.82
    dataset_dir: str = "saves/society_dataset"
    weights_slot: str = "society_mind"
    ingest_traces: bool = False           # opt-in: seed the corpus from external traces
    trace_paths: list = field(default_factory=list)   # your own JSONL trace dirs
    trace_max_samples: int = 2000


@dataclass
class Config:
    world: WorldConfig = field(default_factory=WorldConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    governor: GovernorConfig = field(default_factory=GovernorConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    mind: MindConfig = field(default_factory=MindConfig)


def _section(raw: dict[str, Any], key: str, cls):
    data = raw.get(key) or {}
    known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in data.items() if k in known})


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Read config.yaml. Env var AEON_CONFIG overrides the default path."""
    cfg_path = Path(path or os.environ.get("AEON_CONFIG", DEFAULT_CONFIG))
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text()) or {}
    return Config(
        world=_section(raw, "world", WorldConfig),
        sim=_section(raw, "sim", SimConfig),
        governor=_section(raw, "governor", GovernorConfig),
        server=_section(raw, "server", ServerConfig),
        telemetry=_section(raw, "telemetry", TelemetryConfig),
        persistence=_section(raw, "persistence", PersistenceConfig),
        mind=_section(raw, "mind", MindConfig),
    )
