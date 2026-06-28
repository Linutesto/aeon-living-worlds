"""TeacherCurriculum — gate the student's takeover so it never drives too early.

The student must EARN the population. This is a four-phase curriculum the runtime
consults every tick to decide what share of materialized citizens the liquid student may
drive (the rest stay on the teacher-supervised utility model):

    phase_1_teacher_first   student drives nobody — teacher labels + utility only
    phase_2_guided_student  a small share, under a teacher majority
    phase_3_mixed_policy    a balanced split
    phase_4_student_autonomy up to `autonomy_ratio` of the population

Advancing a phase requires ALL of the gates to clear at once — action / emotion / intent /
target accuracy, a capability floor, and a drift ceiling — so a model that is strong on
actions but hallucinating emotions cannot be promoted. Gates use hysteresis (you enter on
a strict gate, you only fall back on a looser exit gate) so the phase doesn't flicker, and
a *severe* regression (action accuracy collapses or drift spikes) snaps all the way back to
`phase_1_teacher_first`. Rollbacks are counted and surfaced so the takeover is observable
and reversible, never a one-way ratchet.

This object holds no torch state; it reads a plain metrics dict, so it is trivial to unit
test and cannot break the sim.
"""

from __future__ import annotations

from dataclasses import dataclass

PHASES = ["phase_1_teacher_first", "phase_2_guided_student",
          "phase_3_mixed_policy", "phase_4_student_autonomy"]

# the fraction of the population each phase lets the student drive (before the autonomy
# ceiling clamps phase 4). Phases 2/3 are also clamped by the ceiling so a cautious
# operator (low autonomy_ratio) keeps the teacher in the majority throughout.
PHASE_SHARE = [0.0, 0.30, 0.55, 1.0]


@dataclass(frozen=True)
class PhaseGate:
    """All thresholds a metrics snapshot must clear to ENTER a phase."""
    action: float
    emotion: float
    intent: float
    target: float
    capability: float
    max_drift: float

    def passes(self, m: "Metrics") -> bool:
        return (m.action >= self.action and m.emotion >= self.emotion
                and m.intent >= self.intent and m.target >= self.target
                and m.capability >= self.capability and m.drift <= self.max_drift)

    def failures(self, m: "Metrics") -> list[str]:
        out = []
        if m.action < self.action: out.append("action_acc")
        if m.emotion < self.emotion: out.append("emotion_acc")
        if m.intent < self.intent: out.append("intent_acc")
        if m.target < self.target: out.append("target_acc")
        if m.capability < self.capability: out.append("capability")
        if m.drift > self.max_drift: out.append("drift")
        return out


# Gate to ENTER phase index 1, 2, 3 (phase 0 is the start). Each tier is strictly harder.
ENTER_GATES: dict[int, PhaseGate] = {
    1: PhaseGate(action=0.55, emotion=0.42, intent=0.42, target=0.50,
                 capability=0.40, max_drift=0.45),
    2: PhaseGate(action=0.72, emotion=0.56, intent=0.56, target=0.64,
                 capability=0.56, max_drift=0.34),
    3: PhaseGate(action=0.84, emotion=0.68, intent=0.68, target=0.76,
                 capability=0.70, max_drift=0.22),
}
EXIT_RELAX = 0.88          # you keep a phase until metrics fall below 0.88× its enter gate


def _relaxed(g: PhaseGate) -> PhaseGate:
    return PhaseGate(g.action * EXIT_RELAX, g.emotion * EXIT_RELAX,
                     g.intent * EXIT_RELAX, g.target * EXIT_RELAX,
                     g.capability * EXIT_RELAX, min(1.0, g.max_drift / EXIT_RELAX))


@dataclass
class Metrics:
    action: float = 0.0
    emotion: float = 0.0
    intent: float = 0.0
    target: float = 0.0
    capability: float = 0.0
    drift: float = 1.0
    ready: bool = False
    steps: int = 0

    @classmethod
    def from_status(cls, s: dict) -> "Metrics":
        return cls(
            action=float(s.get("action_acc", 0.0)),
            emotion=float(s.get("emotion_acc", 0.0)),
            intent=float(s.get("intent_acc", 0.0)),
            target=float(s.get("target_acc", 0.0)),
            capability=float(s.get("capability_score", 0.0)),
            drift=float(s.get("regression_drift_score", s.get("drift_score", 1.0))),
            ready=bool(s.get("ready", False)),
            steps=int(s.get("steps", 0)),
        )


class TeacherCurriculum:
    def __init__(self, *, warmup_steps: int = 20, autonomy_ratio: float = 0.7,
                 severe_action: float = 0.40, severe_drift: float = 0.60) -> None:
        self.warmup_steps = int(warmup_steps)
        self.autonomy_ratio = max(0.0, min(1.0, float(autonomy_ratio)))
        self.severe_action = severe_action
        self.severe_drift = severe_drift
        self.phase_index = 0
        self.rollbacks = 0
        self.last_blocked: list[str] = []
        self.steps = 0
        self._ready = False

    # ----------------------------------------------------------------- update
    def update(self, status: dict) -> str:
        """Fold a trainer status dict in; return the (possibly new) phase name."""
        m = Metrics.from_status(status)
        self.steps = m.steps
        self._ready = m.ready
        if not m.ready or m.steps < self.warmup_steps:
            # still in the teacher-first warmup window
            self.phase_index = 0
            self.last_blocked = ["warmup"]
            return self.phase

        # severe regression: snap back to teacher-first regardless of current phase
        if self.phase_index > 0 and (m.action < self.severe_action
                                     or m.drift > self.severe_drift):
            self.rollbacks += 1
            self.phase_index = 0
            self.last_blocked = ["severe_regression"]
            return self.phase

        # graceful fall-back: drop a phase while the relaxed exit gate of the CURRENT
        # phase fails (one step at a time so a brief dip doesn't collapse everything).
        while self.phase_index > 0 and not _relaxed(ENTER_GATES[self.phase_index]).passes(m):
            self.phase_index -= 1
            self.rollbacks += 1

        # promotion: advance while the next phase's strict enter gate clears.
        blocked: list[str] = []
        while self.phase_index < len(PHASES) - 1:
            gate = ENTER_GATES[self.phase_index + 1]
            if gate.passes(m):
                self.phase_index += 1
            else:
                blocked = gate.failures(m)
                break
        self.last_blocked = blocked
        return self.phase

    # ---------------------------------------------------------------- queries
    @property
    def phase(self) -> str:
        return PHASES[self.phase_index]

    def student_share(self) -> float:
        if self.phase_index <= 0 or not self._ready:
            return 0.0
        return round(min(self.autonomy_ratio, PHASE_SHARE[self.phase_index]), 3)

    def status(self) -> dict:
        return {
            "phase": self.phase,
            "phase_index": self.phase_index,
            "phase_count": len(PHASES),
            "student_share": self.student_share(),
            "autonomy_ratio": self.autonomy_ratio,
            "warmup_steps": self.warmup_steps,
            "rollbacks": self.rollbacks,
            "blocked_by": list(self.last_blocked),
            "next_gate": _gate_dict(ENTER_GATES.get(self.phase_index + 1)),
        }


def _gate_dict(g: PhaseGate | None) -> dict | None:
    if g is None:
        return None
    return {"action": g.action, "emotion": g.emotion, "intent": g.intent,
            "target": g.target, "capability": g.capability, "max_drift": g.max_drift}
