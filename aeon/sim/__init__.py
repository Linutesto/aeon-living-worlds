"""The deterministic world.

Nothing in this package may import from `governor`, `server`, or `telemetry`.
The sim is a pure function of (seed, params, directives, ticks). That isolation is
what lets us reproduce any world and what keeps the LLM from ever reaching in and
editing outcomes by hand.
"""
