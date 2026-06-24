"""Ollama client for the world-spirit. Model is swappable via config.

Async, single-responsibility: build the request, post to Ollama, return raw text.
Falls back to a deterministic "offline spirit" if Ollama is unreachable so the
whole system still runs (and tests pass) without a model server.
"""

from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger("aeon.llm")


class LLMClient:
    def __init__(self, cfg, *, arbiter=None, default_priority: int = 2,
                 default_label: str = "llm", keep_alive: str | None = None,
                 num_ctx: int | None = None) -> None:
        self.cfg = cfg
        self.model = cfg.model
        self.base_url = cfg.base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=cfg.timeout_seconds)
        self.online: bool | None = None  # learned on first call
        # all calls funnel through one arbiter so the GPU isn't thrashed by concurrent
        # requests and the expensive 27B teacher can preempt cheap journaling.
        self.arbiter = arbiter
        self.default_priority = default_priority
        self.default_label = default_label
        # keep_alive holds the model resident in VRAM between calls (e.g. "15m", -1);
        # this is what stops Ollama reloading the 27B from scratch every cohort.
        self.keep_alive = keep_alive
        # num_ctx caps the context window → caps the KV cache → keeps a big model from
        # spilling to CPU on a 24GB card. Set it just above prompt+output for the teacher.
        self.num_ctx = num_ctx

    async def complete(self, system: str, user: str, format_json: bool = True, *,
                       priority: int | None = None, label: str | None = None,
                       consumer: str | None = None, cache_key: str | None = None,
                       tick: int = 0, meta: dict | None = None, fallback=None,
                       max_wait: float | None = None) -> str:
        """Return the model's text. Uses Ollama /api/chat. `format_json` forces a
        JSON object (for the governor); set False for free prose (interviews).
        Routed through the scheduler (if set) under the given consumer/priority, with
        an optional cheap `fallback` used when the consumer is throttled."""
        prio = self.default_priority if priority is None else priority
        lbl = label or consumer or self.default_label
        if self.arbiter is not None:
            tokens = (len(system) + len(user)) // 4 + getattr(self.cfg, "max_tokens", 0)
            return await self.arbiter.run(
                lambda: self._post(system, user, format_json),
                consumer=consumer, priority=prio, label=lbl, cache_key=cache_key,
                tokens=tokens, tick=tick, meta=meta, fallback=fallback,
                max_wait=max_wait)
        return await self._post(system, user, format_json)

    async def _post(self, system: str, user: str, format_json: bool) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            **({"format": "json"} if format_json else {}),
            **({"keep_alive": self.keep_alive} if self.keep_alive is not None else {}),
            # Disable hidden chain-of-thought: reasoning models (qwen3.x) otherwise
            # burn the whole token budget thinking and return empty content. Harmless
            # for non-reasoning models. Toggle via config.governor.think if needed.
            "think": getattr(self.cfg, "think", False),
            "options": {
                "temperature": self.cfg.temperature,
                "num_predict": self.cfg.max_tokens,
                **({"num_ctx": self.num_ctx} if self.num_ctx else {}),
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            r = await self._client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
            self.online = True
            return r.json().get("message", {}).get("content", "")
        except Exception as e:  # noqa: BLE001 — offline fallback is intentional
            if self.online is not False:
                log.warning("Ollama unreachable (%s); using offline spirit.", e)
            self.online = False
            if not format_json:
                return "(…the words don't come; the connection to the world-mind is lost.)"
            return self._offline(user)

    def _offline(self, user: str) -> str:
        """Deterministic stand-in so the world still breathes without a model.

        Reads the stats it was given and nudges whatever looks worst. Crude on
        purpose — the real spirit is the LLM."""
        directives = []
        goal = "Maintain a living, balanced world."
        if "Biodiversity: low" in user.lower() or "biodiversity: low" in user.lower():
            directives.append({"op": "adjust_param", "key": "mutation_rate",
                               "value": 40, "reason": "diversity collapsing"})
        if "war frequency: high" in user.lower():
            directives.append({"op": "adjust_param", "key": "war_propensity",
                               "value": -30, "reason": "too much war"})
        if not directives:
            directives.append({"op": "adjust_param", "key": "rainfall_multiplier",
                               "value": 5, "reason": "gentle nudge"})
        return json.dumps({
            "thought": "(offline spirit) reacting to the most pressing stat.",
            "goal": goal, "directives": directives, "myth": None,
        })

    async def aclose(self) -> None:
        await self._client.aclose()
