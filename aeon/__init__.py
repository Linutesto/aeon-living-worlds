"""AEON — an AI-governed procedural world.

The package is split into four concerns that never reach across each other except
through small, explicit contracts:

    sim/        deterministic world. Knows nothing about the LLM or the network.
    governor/   the AI world-spirit. Only reads stats and emits directives.
    telemetry/  observation. Reads the world, never mutates it.
    server/     transport. Moves state to the browser and god-actions back in.

The single rule that makes the whole thing work: the governor may only change
`sim.params.WorldParams` and submit validated `governor.directives.Directive`s.
It can never edit world outcomes directly.
"""

__version__ = "0.1.0"
