# Contributing to AEON: Living Worlds

Thanks for your interest! AEON is an experimental living-world simulation and a
work-in-progress, so contributions, bug reports, and ideas are all welcome.

## Ground rules

- **The simulation is the source of truth.** Only `aeon/sim/` may decide world outcomes.
  Everything else (citizens, society, AI, the LLM world-spirit, the renderer) *reads* or
  *nudges* that state through clamped inputs — it must never invent simulation facts.
- **Keep it deterministic.** Use `world.rng.stream("name")` for any randomness in the
  sim/agents/society layers so a seed reproduces a world.
- **Don't break the working sim.** Run the checks below before opening a PR.

## Dev setup

See [docs/INSTALL.md](docs/INSTALL.md). In short:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
```

## Before you open a PR

Run the full verification sweep:

```bash
bash scripts/check.sh
```

This runs `py_compile`, the pytest suite, and `node --check` over the JS. Equivalent
manual steps:

```bash
python -m pytest tests/ -q
node --check web/js/*.js && node --check web/js/omega/*.js
```

Add tests for new behavior (the suite already covers determinism, restart, config
validation, placement, save/load, and the species-mind policies).

## Conventions

- Python: `from __future__ import annotations`, dataclasses for records, module
  docstrings that explain *why*. Match the surrounding file's comment density.
- New tunable knob → add it to `aeon/sim/params.py` `BOUNDS` (with a clamp), read it in
  `aeon/sim/`, and surface it where relevant.
- New serialized data → add a `serialize_*` in `aeon/engine.py`, push it through
  `aeon/server/broadcaster.py` + the WS snapshot, and subscribe in the web client.
- JS has **no build step** — it's loaded via an import map in `web/index.html`. Keep it
  framework-free and mobile-first.
- Update the relevant `docs/*.md` when you change an interface, config key, or constant.

## Reporting bugs

Open an issue with: what you did, what you expected, what happened, your OS/browser/GPU,
and the world seed if relevant. Console logs and a screenshot help a lot.

## Security

Please report security issues privately — see [SECURITY.md](SECURITY.md).
