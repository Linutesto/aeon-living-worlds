"""LiquidSocietyNet — the student: a Closed-form Continuous-time (CfC) recurrent net.

Why liquid / CfC? A citizen is a *time series* — a life unfolds over irregular
life-ticks — so a continuous-time recurrent net is the honest model. CfC (Hasani et
al., 2022) gives the expressivity of a Liquid Time-Constant network in closed form: no
ODE solver, plain backprop, compact and fast. Each CfC cell interpolates between two
learned candidate states with a **time-modulated** sigmoid gate, so the same input at
different dt produces different dynamics — the network reasons about *when*, not just
*what*. Implemented directly in torch (no ncps/torchdiffeq dependency).

Five output heads realize the spec's OUTPUT: action / emotion / future_intent
classifiers plus memory and dialogue embedding regressors.

`DoubleBufferedNet` keeps a frozen **serving** copy for inference (the sim's per-tick
path) and a separate **training** copy the background trainer updates; weights are
swapped under a lock so a forward pass in the event loop never tears mid-update while
training runs in a worker thread. Width/depth scale via config — built to be made big.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import torch
import torch.nn as nn

from . import encode as enc

log = logging.getLogger("aeon.mind.liquid")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


class CfCCell(nn.Module):
    """One closed-form continuous-time cell.

    h' = ff1 · (1 − σ(τ·dt + b)) + ff2 · σ(τ·dt + b)
    where ff1, ff2 and the time-constant τ are all functions of (x, h). The gate is
    modulated by the real time-delta dt, giving genuine continuous-time dynamics.
    """

    def __init__(self, in_dim: int, hidden: int) -> None:
        super().__init__()
        self.backbone = nn.Linear(in_dim + hidden, hidden)
        self.ff1 = nn.Linear(hidden, hidden)
        self.ff2 = nn.Linear(hidden, hidden)
        self.time_a = nn.Linear(hidden, hidden)
        self.time_b = nn.Linear(hidden, hidden)

    def forward(self, x: torch.Tensor, h: torch.Tensor, dt: torch.Tensor) -> torch.Tensor:
        z = torch.tanh(self.backbone(torch.cat([x, h], dim=-1)))
        ff1 = self.ff1(z)
        ff2 = torch.tanh(self.ff2(z))
        gate = torch.sigmoid(self.time_a(z) * dt + self.time_b(z))
        return ff1 * (1.0 - gate) + ff2 * gate


class LiquidSocietyNet(nn.Module):
    def __init__(self, in_dim: int = enc.IN_DIM, hidden: int = 128, layers: int = 2,
                 embed_dim: int = enc.EMBED_DIM) -> None:
        super().__init__()
        self.hidden = hidden
        self.layers = layers
        self.cells = nn.ModuleList(
            [CfCCell(in_dim if i == 0 else hidden, hidden) for i in range(layers)])
        self.head_action = nn.Linear(hidden, enc.N_ACTION)
        self.head_emotion = nn.Linear(hidden, enc.N_EMOTION)
        self.head_intent = nn.Linear(hidden, enc.N_INTENT)
        self.head_memory = nn.Linear(hidden, embed_dim)
        self.head_dialogue = nn.Linear(hidden, embed_dim)

    def init_state(self, batch: int, device=None) -> list[torch.Tensor]:
        device = device or next(self.parameters()).device
        return [torch.zeros(batch, self.hidden, device=device) for _ in self.cells]

    def step(self, x: torch.Tensor, dt: torch.Tensor,
             hs: list[torch.Tensor]) -> tuple[dict, list[torch.Tensor]]:
        """Advance one timestep. x:(B,in_dim) dt:(B,1). Returns heads + new state."""
        inp = x
        new_hs = []
        for i, cell in enumerate(self.cells):
            h = cell(inp, hs[i], dt)
            new_hs.append(h)
            inp = h
        return self._heads(new_hs[-1]), new_hs

    def forward(self, x_seq: torch.Tensor, dt_seq: torch.Tensor,
                hs: list[torch.Tensor] | None = None) -> tuple[dict, list[torch.Tensor]]:
        """x_seq:(B,T,in_dim) dt_seq:(B,T). Unroll the CfC; heads off the final state."""
        b, t, _ = x_seq.shape
        if hs is None:
            hs = self.init_state(b, x_seq.device)
        for k in range(t):
            _, hs = self.step(x_seq[:, k], dt_seq[:, k:k + 1], hs)
        return self._heads(hs[-1]), hs

    def _heads(self, h: torch.Tensor) -> dict:
        return {
            "action": self.head_action(h),
            "emotion": self.head_emotion(h),
            "intent": self.head_intent(h),
            "memory": self.head_memory(h),
            "dialogue": self.head_dialogue(h),
        }

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


class DoubleBufferedNet:
    """A serving copy (eval, inference) + a training copy, with atomic weight swaps."""

    def __init__(self, *, hidden: int = 128, layers: int = 2,
                 device: str = DEVICE) -> None:
        self.device = device
        self.training_net = LiquidSocietyNet(hidden=hidden, layers=layers).to(device)
        self.serving_net = LiquidSocietyNet(hidden=hidden, layers=layers).to(device)
        self.serving_net.load_state_dict(self.training_net.state_dict())
        self.serving_net.eval()
        self._lock = threading.Lock()
        self.version = 0

    def swap(self) -> None:
        """Publish the training weights to the serving net (called after train steps)."""
        sd = {k: v.detach().clone() for k, v in self.training_net.state_dict().items()}
        with self._lock:
            self.serving_net.load_state_dict(sd)
            self.version += 1

    @torch.no_grad()
    def infer(self, x_seq: torch.Tensor, dt_seq: torch.Tensor) -> dict:
        with self._lock:
            heads, _ = self.serving_net(x_seq, dt_seq)
            return {k: v.detach() for k, v in heads.items()}

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({"state": self.training_net.state_dict(),
                    "version": self.version,
                    "hidden": self.training_net.hidden,
                    "layers": self.training_net.layers}, path)

    def load(self, path: str | Path | None) -> bool:
        if path is None or not Path(path).exists():
            return False
        try:
            data = torch.load(path, map_location=self.device, weights_only=False)
            self.training_net.load_state_dict(data["state"])
            self.swap()
            self.version = int(data.get("version", 0))
            return True
        except Exception as e:  # noqa: BLE001 — a stale/incompatible checkpoint is non-fatal
            log.warning("could not load student checkpoint: %s", e)
            return False
