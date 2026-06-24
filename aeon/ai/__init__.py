"""Hierarchical AI.

  Level 1  individuals      cheap utility + traits (agents/traits.py)
  Level 2  groups/species   small neural policies that learn from outcomes (here)
  Level 3  civilizations    occasional strategic LLM reasoning (governor, reused)
  Level 4  world spirit     the governor that bends global pressures

This package is Level 2: a dedicated, continuously-learning policy per species that
biases how its individuals choose actions. It runs on the GPU via PyTorch when
available and transparently falls back to a numpy policy with the same interface so
the simulation never hard-depends on the ML stack being installed.
"""
