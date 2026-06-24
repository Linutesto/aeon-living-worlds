"""God Console routes: player-issued interventions.

These map friendly button presses to the same safe directive path the spirit uses.
Nothing here edits the world directly — it all funnels through Engine.god_action.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .schemas import GodAction, ActionResult

router = APIRouter(prefix="/api/god", tags=["god"])

# Friendly presets surfaced as buttons in the dashboard God Console.
PRESETS = [
    {"label": "Trigger Meteor", "op": "trigger_event", "kind": "meteor_impact"},
    {"label": "Start Ice Age", "op": "trigger_event", "kind": "ice_age"},
    {"label": "Unleash Plague", "op": "trigger_event", "kind": "plague"},
    {"label": "Resource Boom", "op": "trigger_event", "kind": "resource_boom"},
    {"label": "Magical Anomaly", "op": "trigger_event", "kind": "magical_anomaly"},
    {"label": "Erupt Volcano", "op": "trigger_event", "kind": "volcanic_eruption"},
    {"label": "Cause Famine", "op": "trigger_event", "kind": "drought"},
    {"label": "Great Flood", "op": "trigger_event", "kind": "flood"},
    {"label": "Increase Rainfall +25%", "op": "adjust_param",
     "key": "rainfall_multiplier", "value": 25},
    {"label": "Reduce Predators -30%", "op": "adjust_param",
     "key": "predator_fertility", "value": -30},
    {"label": "Boost Mutation +50%", "op": "adjust_param",
     "key": "mutation_rate", "value": 50},
    {"label": "Spawn Predator", "op": "spawn_species", "diet": "predator"},
    {"label": "Spawn Plant", "op": "spawn_species", "diet": "plant"},
]


@router.get("/presets")
async def presets() -> list[dict]:
    return PRESETS


@router.post("/action", response_model=ActionResult)
async def action(req: Request, body: GodAction) -> ActionResult:
    engine = req.app.state.engine
    result = engine.god_action(body.op, **body.payload())
    return ActionResult(**result)
