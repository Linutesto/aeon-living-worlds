"""Omega render projection.

This package turns simulation truth into chunked render facts. It does not create
simulation state; it only projects terrain, cities, buildings, citizens, policies,
and history into payloads the Three.js client can stream.
"""

from .projection import (
    chunk_payload,
    entity_payload,
    manifest_payload,
    policy_counterfactual,
    policy_inspector,
)

__all__ = [
    "chunk_payload",
    "entity_payload",
    "manifest_payload",
    "policy_counterfactual",
    "policy_inspector",
]
