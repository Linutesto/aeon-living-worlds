"""LiquidSocietyNet — the student: a Closed-form Continuous-time (CfC) recurrent net.

Why liquid / CfC? A citizen is a *time series* — a life unfolds over irregular
life-ticks — so a continuous-time recurrent net is the honest model. CfC (Hasani et
al., 2022) gives the expressivity of a Liquid Time-Constant network in closed form: no
ODE solver, plain backprop, compact and fast. Each CfC cell interpolates between two
learned candidate states with a **time-modulated** sigmoid gate, so the same input at
different dt produces different dynamics — the network reasons about *when*, not just
*what*. Implemented directly in torch (no ncps/torchdiffeq dependency).

Five output heads realize the spec's OUTPUT: action / emotion / future_intent
classifiers, spatial target kind, plus memory and dialogue embedding regressors.

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

# Named student sizes (hidden, layers) tuned to the spec's parameter targets. The exact
# count shifts a little with enc.IN_DIM (the per-step input width), so these aim at the
# centre of each band; `dims_for_size` is the single source of truth and `n_params()`
# reports the real number. Cognition is TIERED — most citizens never run the big student
# (see runtime.HybridMind.active_embodied_ratio): only a bounded set of embodied citizens
# do, so a "large" student is affordable on one GPU.
MODEL_SIZES: dict[str, tuple[int, int]] = {
    "tiny": (240, 2),     # ~0.7M  — the historical default; routine cognition
    "small": (352, 4),    # ~3M
    "medium": (592, 5),   # ~10M
    "large": (720, 5),    # ~15M   — only for selected embodied citizens
}
DEFAULT_SIZE = "tiny"

# Human-facing parameter-count labels for each named size (the dashboard shows these in
# the model picker). `n_params()` reports the exact live count once a net is built.
SIZE_LABELS: dict[str, str] = {
    "tiny": "~0.7M", "small": "~3M", "medium": "~10M", "large": "~15M",
}
# Safety rails for raw hidden/layers overrides driven from the UI (keep one GPU forward
# cheap and the net trainable). The dashboard clamps to these too.
HIDDEN_BOUNDS = (32, 1024)
LAYERS_BOUNDS = (1, 8)


def dims_for_size(size: str | None) -> tuple[int, int]:
    """Resolve a named size (or None) to (hidden, layers). Unknown → tiny."""
    return MODEL_SIZES.get((size or DEFAULT_SIZE).lower(), MODEL_SIZES[DEFAULT_SIZE])


def model_options() -> list[dict]:
    """The selectable named sizes for the dashboard model picker (size→dims+label)."""
    return [{"size": name, "hidden": h, "layers": l, "label": SIZE_LABELS.get(name, "")}
            for name, (h, l) in MODEL_SIZES.items()]


def clamp_dims(hidden: int | None, layers: int | None) -> tuple[int | None, int | None]:
    """Clamp raw UI overrides into the safe band (None passes through untouched)."""
    if hidden is not None:
        hidden = max(HIDDEN_BOUNDS[0], min(HIDDEN_BOUNDS[1], int(hidden)))
    if layers is not None:
        layers = max(LAYERS_BOUNDS[0], min(LAYERS_BOUNDS[1], int(layers)))
    return hidden, layers


def resolve_dims(*, size: str | None = None, hidden: int | None = None,
                 layers: int | None = None) -> tuple[int, int]:
    """A named `size` wins; explicit hidden/layers override individual dims; else tiny.

    This lets config say `student_size: medium` OR pin raw `hidden`/`layers` for an
    experiment, without the two interpretations fighting."""
    h, l = dims_for_size(size)
    if hidden is not None:
        h = int(hidden)
    if layers is not None:
        l = int(layers)
    return h, l


class CfCCell(nn.Module):
    """One closed-form continuous-time cell.

    h' = ff1 · (1 − σ(τ·dt + b)) + ff2 · σ(τ·dt + b)
    where ff1, ff2 and the time-constant τ are all functions of (x, h). The gate is
    modulated by the real time-delta dt, giving genuine continuous-time dynamics.
    """

    def __init__(self, in_dim: int, hidden: int) -> None:
        super().__init__()
        self.backbone = nn.Linear(in_dim + hidden, hidden)
        # Normalize the recurrent pre-activation. Without it the fed-back state makes the
        # cell's trainability seed-fragile — for some inits the recurrence washes out the
        # input and the heads collapse toward a single class. LayerNorm stabilizes the
        # dynamics so the net reliably learns the input→label mapping across seeds.
        self.ln = nn.LayerNorm(hidden)
        self.ff1 = nn.Linear(hidden, hidden)
        self.ff2 = nn.Linear(hidden, hidden)
        self.time_a = nn.Linear(hidden, hidden)
        self.time_b = nn.Linear(hidden, hidden)

    def forward(self, x: torch.Tensor, h: torch.Tensor, dt: torch.Tensor) -> torch.Tensor:
        z = torch.tanh(self.ln(self.backbone(torch.cat([x, h], dim=-1))))
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
        self.head_target = nn.Linear(hidden, enc.N_TARGET)
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
            "target": self.head_target(h),
            "memory": self.head_memory(h),
            "dialogue": self.head_dialogue(h),
        }

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


class DoubleBufferedNet:
    """A serving copy (eval, inference) + a training copy, with atomic weight swaps."""

    def __init__(self, *, hidden: int | None = None, layers: int | None = None,
                 size: str | None = None, device: str = DEVICE) -> None:
        self.device = device
        h, l = resolve_dims(size=size, hidden=hidden, layers=layers)
        self.size = (size or DEFAULT_SIZE).lower()
        self.hidden, self.layers = h, l
        self.training_net = LiquidSocietyNet(hidden=h, layers=l).to(device)
        self.serving_net = LiquidSocietyNet(hidden=h, layers=l).to(device)
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
                    "size": self.size,
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
