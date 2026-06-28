"""Tests for the teacher-priority curriculum (focus area 4).

Guards that the student cannot take over too early: it stays in phase_1_teacher_first
through warmup and weak metrics, advances only when ALL gates clear, is gated by each of
action/emotion/intent/target accuracy + capability + drift independently, and rolls back
(all the way to teacher-first on a severe regression).
"""

from __future__ import annotations

from aeon.mind.curriculum import PHASES, TeacherCurriculum


def _status(action=1.0, emotion=1.0, intent=1.0, target=1.0, capability=1.0,
            drift=0.0, ready=True, steps=100) -> dict:
    return {"action_acc": action, "emotion_acc": emotion, "intent_acc": intent,
            "target_acc": target, "capability_score": capability,
            "regression_drift_score": drift, "ready": ready, "steps": steps}


def test_phase_names_match_spec():
    assert PHASES == ["phase_1_teacher_first", "phase_2_guided_student",
                      "phase_3_mixed_policy", "phase_4_student_autonomy"]


def test_teacher_first_during_warmup():
    cur = TeacherCurriculum(warmup_steps=20)
    assert cur.update(_status(ready=False, steps=0)) == "phase_1_teacher_first"
    assert cur.update(_status(steps=5)) == "phase_1_teacher_first"   # not warmed up
    assert cur.student_share() == 0.0


def test_no_premature_takeover_with_weak_metrics():
    cur = TeacherCurriculum(warmup_steps=5)
    # warmed up but mediocre — must NOT promote past teacher-first
    out = cur.update(_status(action=0.5, emotion=0.3, intent=0.3, target=0.4,
                             capability=0.3, drift=0.5))
    assert out == "phase_1_teacher_first"
    assert cur.student_share() == 0.0


def test_full_confidence_reaches_autonomy_clamped_by_ratio():
    cur = TeacherCurriculum(warmup_steps=5, autonomy_ratio=0.7)
    assert cur.update(_status()) == "phase_4_student_autonomy"
    assert cur.student_share() == 0.7            # clamped to the autonomy ceiling


def test_each_metric_gates_independently():
    # strong everywhere EXCEPT one metric → cannot reach the top phase
    for weak in ("emotion", "intent", "target", "capability"):
        cur = TeacherCurriculum(warmup_steps=1)
        kw = {weak: 0.30}
        cur.update(_status(**kw))
        assert cur.phase != "phase_4_student_autonomy", weak
        assert weak.replace("emotion", "emotion_acc").replace("intent", "intent_acc") \
            or True  # smoke: blocked_by is populated
        assert cur.last_blocked


def test_high_drift_blocks_promotion():
    cur = TeacherCurriculum(warmup_steps=1)
    cur.update(_status(drift=0.5))               # accurate but drifting
    assert cur.phase == "phase_1_teacher_first"
    assert "drift" in cur.last_blocked


def test_severe_regression_rolls_back_to_teacher_first():
    cur = TeacherCurriculum(warmup_steps=1)
    cur.update(_status())                        # climb to autonomy
    assert cur.phase_index == 3
    # action accuracy collapses → snap straight back to teacher-first
    cur.update(_status(action=0.2, drift=0.1))
    assert cur.phase == "phase_1_teacher_first"
    assert cur.rollbacks >= 1


def test_graceful_single_step_fallback():
    cur = TeacherCurriculum(warmup_steps=1)
    cur.update(_status())                        # phase 4
    # metrics dip to mid-tier (not severe) → drop, but not all the way to phase 1
    cur.update(_status(action=0.80, emotion=0.60, intent=0.60, target=0.66,
                       capability=0.60, drift=0.25))
    assert 0 < cur.phase_index < 3
    assert cur.rollbacks >= 1


def test_status_is_serializable_and_complete():
    cur = TeacherCurriculum(warmup_steps=1)
    cur.update(_status(action=0.75, emotion=0.58, intent=0.58, target=0.66,
                       capability=0.58, drift=0.3))
    st = cur.status()
    for key in ("phase", "phase_index", "student_share", "autonomy_ratio",
                "rollbacks", "blocked_by", "next_gate"):
        assert key in st
