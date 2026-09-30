"""Exponential forgetting and temporal decay for MasteryFlow.
Pure Python, no framework imports.
"""

from __future__ import annotations
import math
from typing import Tuple
from engine.types import MasteryState, MasteryStatus
from engine.params import EngineConfig, DEFAULT_CONFIG


def apply_forgetting(
    state: MasteryState,
    current_time: float,
    config: EngineConfig = DEFAULT_CONFIG,
) -> MasteryState:
    """Applies exponential forgetting decay to mastery and grows uncertainty
    based on elapsed time since the last evidence was recorded.
    """
    delta_sec = current_time - state.last_evidence_at
    if delta_sec <= 0:
        return state

    half_life_sec = config.half_life_days * 86400.0
    lam = math.log(2.0) / half_life_sec

    # Exponential decay towards asymptotic floor
    p_floor = config.floor_mastery
    if state.p_mastery > p_floor:
        decayed_p = p_floor + (state.p_mastery - p_floor) * math.exp(-lam * delta_sec)
    else:
        decayed_p = state.p_mastery

    # Epistemic uncertainty grows towards 1.0 with elapsed time
    # (Uncertainty half-growth is set proportionally to half-life)
    lam_u = math.log(2.0) / (half_life_sec * 1.5)
    growing_u = 1.0 - (1.0 - state.uncertainty) * math.exp(-lam_u * delta_sec)

    decayed_p = max(0.05, min(0.98, decayed_p))
    growing_u = max(0.05, min(0.98, growing_u))

    # Re-evaluate status under decay
    status = state.status
    if state.status == MasteryStatus.MASTERED and decayed_p < config.mastery_threshold:
        status = MasteryStatus.PROFICIENT
    if status in (MasteryStatus.PROFICIENT, MasteryStatus.MASTERED) and decayed_p < 0.60:
        status = MasteryStatus.LEARNING

    return MasteryState(
        student_id=state.student_id,
        concept_id=state.concept_id,
        p_mastery=round(decayed_p, 4),
        uncertainty=round(growing_u, 4),
        has_passed_transfer=state.has_passed_transfer,
        highest_difficulty_passed=state.highest_difficulty_passed,
        last_evidence_at=state.last_evidence_at,  # Keeps original timestamp of evidence
        status=status,
        consecutive_failures=state.consecutive_failures,
        total_attempts=state.total_attempts,
        recent_history=state.recent_history.copy(),
        history_weights=state.history_weights.copy(),
    )


def is_long_gap(
    delta_seconds: float,
    config: EngineConfig = DEFAULT_CONFIG,
) -> bool:
    """Checks whether the elapsed time exceeds the long-gap threshold (e.g. 7 days)."""
    return delta_seconds >= config.long_gap_seconds


def classify_failure_mode(
    state_before_attempt: MasteryState,
    attempt_time: float,
    config: EngineConfig = DEFAULT_CONFIG,
) -> Tuple[str, str]:
    """Determines whether a failure is caused by 'forgetting' (spaced decay)
    or 'not_learned' (conceptual deficiency).
    Returns (mode, rationale_explanation).
    """
    delta_sec = attempt_time - state_before_attempt.last_evidence_at
    if is_long_gap(delta_sec, config) and state_before_attempt.p_mastery >= 0.45:
        days = round(delta_sec / 86400.0, 1)
        return (
            "forgetting",
            f"Failure after long gap of {days} days with prior mastery {round(state_before_attempt.p_mastery * 100)}%: categorized as retention decay (Review).",
        )
    return (
        "not_learned",
        f"Failure with active practice (last seen {round(delta_sec / 3600.0, 1)}h ago): categorized as learning deficiency.",
    )
