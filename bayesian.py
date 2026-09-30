"""Bayesian Knowledge Tracing and Evidence Weighting for MasteryFlow.
Pure Python, no framework imports.
"""

from __future__ import annotations
import math
from typing import Tuple, Optional
from engine.types import (
    Attempt,
    MasteryState,
    MasteryStatus,
    Question,
    QuestionType,
)
from engine.params import EngineConfig, DEFAULT_CONFIG


def compute_slip_guess(
    difficulty: int,
    q_type: QuestionType,
    config: EngineConfig = DEFAULT_CONFIG,
) -> Tuple[float, float]:
    """Derives slip and guess parameters from item difficulty (1-5) and type.
    Higher difficulty increases slip and decreases guess.
    """
    type_str = q_type.value
    base_guess = config.base_guesses.get(type_str, 0.20)
    base_slip = config.base_slips.get(type_str, 0.12)

    # Guess decreases with difficulty
    diff_factor = max(0, difficulty - 1)
    guess = base_guess * max(0.20, 1.0 - 0.12 * diff_factor)
    guess = max(0.02, min(0.35, guess))

    # Slip increases with difficulty
    slip = base_slip + 0.04 * diff_factor
    slip = max(0.05, min(0.38, slip))

    return round(slip, 4), round(guess, 4)


def compute_evidence_weight(
    attempt: Attempt,
    previous_attempt_on_item: Optional[Attempt] = None,
    q_type: QuestionType = QuestionType.PROCEDURAL,
    config: EngineConfig = DEFAULT_CONFIG,
) -> Tuple[float, str]:
    """Computes evidence weight w in [0, 1] based on:
    - Retries within 60s (counts as at most one piece of evidence, w = 0.0)
    - Consecutive attempt count penalty on same item (0.5 ** (attempt_no - 1))
    - Hints used penalty
    - Plausibility floor (rapid response penalty)
    - Low confidence discount on correct answers
    """
    # 1. 60-second rapid retry check
    if previous_attempt_on_item is not None:
        delta_sec = attempt.created_at - previous_attempt_on_item.created_at
        if 0.0 <= delta_sec <= config.rapid_retry_window_seconds:
            return 0.0, f"Rapid retry within {round(delta_sec, 1)}s (<60s window): weight zeroed"

    # 2. Hints penalty
    f_hints = config.hint_penalties.get(attempt.hints_used, 0.15)

    # 3. Retry penalty on repeated attempts
    f_retry = 0.5 ** max(0, attempt.attempt_no - 1)

    # 4. Speed / plausibility floor
    floor_ms = (
        config.rapid_guess_recall_ms
        if q_type == QuestionType.RECALL
        else config.rapid_guess_procedural_ms
    )
    if attempt.response_ms < floor_ms:
        f_speed = max(0.10, attempt.response_ms / float(floor_ms))
    else:
        f_speed = 1.0

    # 5. Confidence calibration discount
    if attempt.correct and attempt.confidence == 1:
        f_confidence = config.low_confidence_discount
    else:
        f_confidence = 1.0

    w = f_hints * f_retry * f_speed * f_confidence
    w = max(0.0, min(1.0, w))

    breakdown = (
        f"w={round(w, 3)} [hints({attempt.hints_used})={f_hints}, "
        f"retry(#{attempt.attempt_no})={round(f_retry, 2)}, "
        f"speed({attempt.response_ms}ms)={round(f_speed, 2)}, "
        f"conf({attempt.confidence})={f_confidence}]"
    )
    return round(w, 4), breakdown


def update_mastery_bayesian(
    state: MasteryState,
    question: Question,
    attempt: Attempt,
    previous_attempt_on_item: Optional[Attempt] = None,
    config: EngineConfig = DEFAULT_CONFIG,
) -> Tuple[MasteryState, float, str]:
    """Applies Bayesian Knowledge Tracing update with evidence weighting,
    difficulty cap, transfer item constraints, and uncertainty tracking.
    """
    weight, weight_reason = compute_evidence_weight(
        attempt=attempt,
        previous_attempt_on_item=previous_attempt_on_item,
        q_type=question.type,
        config=config,
    )

    attempt.weight_applied = weight

    # If weight is 0 (e.g. rapid repeat spam), state remains unchanged
    if weight == 0.0:
        return state, 0.0, f"No state update: {weight_reason}"

    p = state.p_mastery
    slip, guess = compute_slip_guess(question.difficulty, question.type, config)

    # Standard BKT posterior
    if attempt.correct:
        # P(L | Y = 1) = [p * (1 - S)] / [p * (1 - S) + (1 - p) * G]
        num = p * (1.0 - slip)
        denom = num + (1.0 - p) * guess
        posterior = num / max(1e-9, denom)
    else:
        # P(L | Y = 0) = [p * S] / [p * S + (1 - p) * (1 - G)]
        num = p * slip
        denom = num + (1.0 - p) * (1.0 - guess)
        posterior = num / max(1e-9, denom)

    # Weighted update
    delta_p = weight * (posterior - p)
    p_new = p + delta_p

    # Tracking transfer and difficulty progression
    has_passed_transfer = state.has_passed_transfer or (
        attempt.correct and question.type == QuestionType.TRANSFER
    )
    highest_diff = max(
        state.highest_difficulty_passed,
        question.difficulty if attempt.correct else 0,
    )

    # Transfer failure penalty:
    # "Failing a transfer question after several easy successes lowers p_mastery and raises uncertainty"
    u_new = state.uncertainty
    if not attempt.correct and question.type == QuestionType.TRANSFER:
        # Penalty scaling if student had moderate-to-high mastery
        transfer_penalty = 0.12 * weight * (1.0 + (p - 0.5))
        p_new = max(0.10, p_new - max(0.04, transfer_penalty))
        # Epistemic uncertainty increases upon unexpected failure on transfer
        u_new = min(0.95, state.uncertainty + 0.18 * weight)
    else:
        # Uncertainty decreases as evidence accumulates
        u_new = max(0.05, state.uncertainty * (1.0 - 0.22 * weight))

    # Difficulty cap:
    # "Correct on difficulty 1-2 items can raise mastery only up to a cap until a transfer or difficulty>=3 item is answered correctly."
    has_unlocked_cap = has_passed_transfer or (highest_diff >= 3)
    applied_cap = False
    if not has_unlocked_cap and p_new > config.easy_cap:
        p_new = config.easy_cap
        applied_cap = True

    # Clamp values
    p_new = max(0.05, min(0.98, p_new))
    u_new = max(0.05, min(0.95, u_new))

    # Consecutive failures count
    new_consecutive_failures = 0 if attempt.correct else (state.consecutive_failures + 1)
    new_total_attempts = state.total_attempts + 1

    # Recent history tracking (last 10)
    updated_history = (state.recent_history + [attempt.correct])[-10:]
    updated_weights = (state.history_weights + [weight])[-10:]

    # Status derivation:
    # "status can never become 'mastered' without a passed transfer item"
    if p_new >= config.mastery_threshold and has_passed_transfer and u_new <= config.uncertainty_threshold:
        new_status = MasteryStatus.MASTERED
    elif p_new >= config.mastery_threshold:
        new_status = MasteryStatus.PROFICIENT
    elif p_new >= 0.45:
        new_status = MasteryStatus.LEARNING
    elif new_consecutive_failures >= 2 or p_new < 0.35:
        new_status = MasteryStatus.STRUGGLING
    else:
        new_status = MasteryStatus.LEARNING

    new_state = MasteryState(
        student_id=state.student_id,
        concept_id=state.concept_id,
        p_mastery=round(p_new, 4),
        uncertainty=round(u_new, 4),
        has_passed_transfer=has_passed_transfer,
        highest_difficulty_passed=highest_diff,
        last_evidence_at=attempt.created_at,
        status=new_status,
        consecutive_failures=new_consecutive_failures,
        total_attempts=new_total_attempts,
        recent_history=updated_history,
        history_weights=updated_weights,
    )

    log_desc = (
        f"Bayes update: p={round(p, 3)}->{round(p_new, 3)} (delta={round(delta_p, 3)}), "
        f"u={round(state.uncertainty, 3)}->{round(u_new, 3)}, {weight_reason}"
        + (" [Cap applied: diff<=2 without transfer]" if applied_cap else "")
    )

    return new_state, round(delta_p, 4), log_desc
