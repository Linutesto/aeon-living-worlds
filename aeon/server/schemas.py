"""Pydantic request/response models for the REST surface."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SpeedRequest(BaseModel):
    speed: float = Field(ge=0, le=100, description="0 = paused; 1..100x")


class SaveRequest(BaseModel):
    slot: str = Field(default="manual", min_length=1, max_length=48)


class GodAction(BaseModel):
    op: str = Field(description="directive op, e.g. trigger_event / adjust_param")
    # free-form payload mirroring directives.Directive; validated downstream
    key: str | None = None
    value: float | None = None
    kind: str | None = None
    diet: str | None = None
    duration: int | None = None
    name: str | None = None

    def payload(self) -> dict:
        return {k: v for k, v in self.model_dump().items()
                if k != "op" and v is not None}


class ActionResult(BaseModel):
    ok: bool
    message: str
