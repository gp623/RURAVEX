"""Next-action decision engine, explainability generator, counterfactuals,
and deterministic replay. Pure Python, no framework imports.
"""

from __future__ import annotations
import uuid
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timezone

from engine.types import (
    Action,
    Attempt,
    CounterfactualResult,
    Decision,
    MasteryState,
    MasteryStatus,
    Question,
    QuestionType,
)
from engine.params import EngineConfig, DEFAULT_CONFIG
from engine.graph import CurriculumGraph, DEFAULT_CONCEPTS
from engine.bayesian import update_mastery_bayesian
from engine.forgetting import apply_forgetting, classify_failure_mode


def is_stagnating(state: MasteryState, config: EngineConfig = DEFAULT_CONFIG) -> bool:
    """Detects if student's recent performance is oscillating or stagnating."""
    if state.total_attempts < config.stagnation_attempts:
        return False
    recent = state.recent_history[-config.stagnation_attempts:]
    # True if oscillating (e.g. [True, False, True, False, True]) with low net gain
    changes = sum(1 for i in range(len(recent) - 1) if recent[i] != recent[i + 1])
    return changes >= 3 and state.p_mastery < config.mastery_threshold


def determine_next_action(
    current_concept_id: str,
    student_states: Dict[str, MasteryState],
    graph: CurriculumGraph,
    config: EngineConfig = DEFAULT_CONFIG,
    last_failure_mode: Optional[str] = None,
) -> Tuple[Action, str, str]:
    """Determines the next pedagogical action for the student.
    Returns:
        (action, target_concept_id, numeric_reason_string)
    """
    state = student_states.get(current_concept_id)
    if not state:
        return (
            Action.PRACTICE,
            current_concept_id,
            f"Begin initial assessment for {current_concept_id}: initial prior p=15%, uncertainty=85%.",
        )

    # 1. Teacher Intervention check
    if state.consecutive_failures >= config.stuck_cycles_threshold:
        return (
            Action.INTERVENTION,
            current_concept_id,
            (
                f"Teacher Intervention required: {state.consecutive_failures} consecutive failed cycles "
                f"on {current_concept_id}. Mastery dropped to {round(state.p_mastery * 100)}% "
                f"(±{round(state.uncertainty * 100)}%). Student is stuck in struggle state."
            ),
        )

    if (
        state.total_attempts >= config.intervention_high_uncertainty_attempts
        and state.uncertainty >= config.intervention_high_uncertainty_threshold
    ):
        return (
            Action.INTERVENTION,
            current_concept_id,
            (
                f"Teacher Intervention required: persistent epistemic uncertainty of {round(state.uncertainty * 100)}% "
                f"after {state.total_attempts} attempts on {current_concept_id}. Algorithmic confidence insufficient."
            ),
        )

    if is_stagnating(state, config):
        return (
            Action.INTERVENTION,
            current_concept_id,
            (
                f"Teacher Intervention required: performance stagnation detected over last "
                f"{config.stagnation_attempts} attempts on {current_concept_id}. Mastery stagnant at "
                f"{round(state.p_mastery * 100)}% with alternating oscillations."
            ),
        )

    # 2. Prerequisite Conflict check
    is_ready, weakest_ancestor, unsatisfied = graph.check_prerequisites(
        current_concept_id, student_states, config
    )
    if not is_ready and weakest_ancestor:
        weakest_state = student_states.get(weakest_ancestor)
        weakest_p = round((weakest_state.p_mastery if weakest_state else 0.15) * 100)
        weakest_u = round((weakest_state.uncertainty if weakest_state else 0.85) * 100)
        return (
            Action.REMEDIATE,
            weakest_ancestor,
            (
                f"Remediate Prerequisite before {current_concept_id}: ancestor '{weakest_ancestor}' "
                f"mastery is {weakest_p}% (±{weakest_u}%), below required threshold of "
                f"{round(config.prereq_readiness_threshold * 100)}%. Diverting to weakest dependency."
            ),
        )

    # 3. Spaced Review check (decay or long-gap retention loss)
    if last_failure_mode == "forgetting" or (
        state.status in (MasteryStatus.PROFICIENT, MasteryStatus.MASTERED)
        and state.p_mastery < config.mastery_threshold
    ):
        return (
            Action.REVIEW,
            current_concept_id,
            (
                f"Review {current_concept_id}: retention decay detected. Mastery currently at "
                f"{round(state.p_mastery * 100)}% (±{round(state.uncertainty * 100)}%), previously "
                f"mastered. Spaced review scheduled to rebuild retrieval strength."
            ),
        )

    # 4. Advance / Challenge check
    if state.p_mastery >= config.mastery_threshold and state.has_passed_transfer:
        # Search for next ready descendant in curriculum
        children = graph.adj_children.get(current_concept_id, [])
        for child_id in children:
            child_ready, _, _ = graph.check_prerequisites(child_id, student_states, config)
            child_state = student_states.get(child_id)
            if child_ready and (not child_state or child_state.p_mastery < config.mastery_threshold):
                return (
                    Action.ADVANCE,
                    child_id,
                    (
                        f"Advance to next topic {child_id}: current topic {current_concept_id} is "
                        f"fully mastered at {round(state.p_mastery * 100)}% (±{round(state.uncertainty * 100)}%) "
                        f"with verified transfer completion. Prerequisites satisfied."
                    ),
                )

        # If no forward child or all mastered, offer Challenge
        return (
            Action.CHALLENGE,
            current_concept_id,
            (
                f"Challenge level on {current_concept_id}: concept mastered at {round(state.p_mastery * 100)}% "
                f"(±{round(state.uncertainty * 100)}%). Recommend difficulty-5 synthesis problems."
            ),
        )

    # 5. Default Practice
    missing_transfer = not state.has_passed_transfer and state.p_mastery >= config.easy_cap
    if missing_transfer:
        reason_extra = (
            f"Mastery capped at {round(state.p_mastery * 100)}% until a transfer question is passed."
        )
    else:
        reason_extra = (
            f"Current mastery {round(state.p_mastery * 100)}% (±{round(state.uncertainty * 100)}%) "
            f"in progress towards {round(config.mastery_threshold * 100)}% proficiency threshold."
        )

    return (
        Action.PRACTICE,
        current_concept_id,
        f"Practice {current_concept_id}: {reason_extra}",
    )


def generate_counterfactual(
    student_id: str,
    current_concept_id: str,
    current_action: Action,
    target_concept_id: str,
    student_states: Dict[str, MasteryState],
    graph: CurriculumGraph,
    config: EngineConfig = DEFAULT_CONFIG,
) -> CounterfactualResult:
    """Generates an actionable counterfactual statement explaining the exact
    evidence needed to alter the current recommendation.
    """
    state = student_states.get(current_concept_id)
    curr_p = state.p_mastery if state else 0.15

    if current_action == Action.INTERVENTION:
        return CounterfactualResult(
            current_action=current_action,
            counterfactual_action=Action.PRACTICE,
            required_condition="1 guided attempt with teacher reset or 1 correct answer without hints",
            target_concept_id=current_concept_id,
            projected_mastery=curr_p + 0.10,
            explanation=(
                f"A single unprompted correct response on {current_concept_id} will clear the consecutive "
                f"error counter and return recommendation from Teacher Intervention to Practice."
            ),
        )

    if current_action == Action.REMEDIATE:
        weakest_id = target_concept_id
        target_state = student_states.get(weakest_id)
        target_p = target_state.p_mastery if target_state else 0.15
        needed_points = round((config.prereq_readiness_threshold - target_p) * 100)
        return CounterfactualResult(
            current_action=current_action,
            counterfactual_action=Action.PRACTICE,
            required_condition=f"Score >= {round(config.prereq_readiness_threshold * 100)}% on {weakest_id} (+{max(5, needed_points)}% gain)",
            target_concept_id=current_concept_id,
            projected_mastery=config.prereq_readiness_threshold,
            explanation=(
                f"Raising prerequisite '{weakest_id}' mastery from {round(target_p * 100)}% to "
                f"{round(config.prereq_readiness_threshold * 100)}% will clear the prerequisite blocker "
                f"and resume Practice on {current_concept_id}."
            ),
        )

    if current_action == Action.PRACTICE:
        if state and not state.has_passed_transfer:
            return CounterfactualResult(
                current_action=current_action,
                counterfactual_action=Action.ADVANCE,
                required_condition="1 correct transfer answer (difficulty >= 3) without hints",
                target_concept_id=current_concept_id,
                projected_mastery=max(0.82, curr_p + 0.12),
                explanation=(
                    f"Solving 1 transfer problem on {current_concept_id} lifts the difficulty cap, "
                    f"raising projected mastery to {round(max(0.82, curr_p + 0.12) * 100)}% and advancing to next concept."
                ),
            )
        else:
            needed = max(1, int(round((config.mastery_threshold - curr_p) / 0.10)))
            return CounterfactualResult(
                current_action=current_action,
                counterfactual_action=Action.ADVANCE,
                required_condition=f"{needed} consecutive correct answers without hints",
                target_concept_id=current_concept_id,
                projected_mastery=config.mastery_threshold + 0.05,
                explanation=(
                    f"{needed} correct answers on {current_concept_id} will push mastery over "
                    f"{round(config.mastery_threshold * 100)}% and trigger Advance."
                ),
            )

    if current_action == Action.REVIEW:
        return CounterfactualResult(
            current_action=current_action,
            counterfactual_action=Action.ADVANCE,
            required_condition="1 successful review recall check",
            target_concept_id=current_concept_id,
            projected_mastery=config.mastery_threshold + 0.04,
            explanation=(
                f"Successfully answering 1 review question on {current_concept_id} restores "
                f"retrieval strength to >{round(config.mastery_threshold * 100)}% and returns to Advance."
            ),
        )

    # Fallback / Challenge / Advance
    return CounterfactualResult(
        current_action=current_action,
        counterfactual_action=Action.CHALLENGE,
        required_condition="Complete 1 difficulty-5 extension problem",
        target_concept_id=current_concept_id,
        projected_mastery=0.95,
        explanation=f"Maintaining >80% mastery on {current_concept_id} qualifies for advanced synthesis challenges.",
    )


def evaluate_and_record_decision(
    student_id: str,
    current_concept_id: str,
    student_states: Dict[str, MasteryState],
    graph: CurriculumGraph,
    evidence_ids: List[str],
    config: EngineConfig = DEFAULT_CONFIG,
    last_failure_mode: Optional[str] = None,
) -> Decision:
    """Evaluates the state snapshot and produces an explainable, reproducible Decision object."""
    action, target_id, reason = determine_next_action(
        current_concept_id=current_concept_id,
        student_states=student_states,
        graph=graph,
        config=config,
        last_failure_mode=last_failure_mode,
    )

    counterfactual = generate_counterfactual(
        student_id=student_id,
        current_concept_id=current_concept_id,
        current_action=action,
        target_concept_id=target_id,
        student_states=student_states,
        graph=graph,
        config=config,
    )

    # Snapshot of all states
    snapshot = {c_id: state.to_dict() for c_id, state in student_states.items()}

    return Decision(
        id=str(uuid.uuid4()),
        student_id=student_id,
        action=action,
        target_concept_id=target_id,
        reason=reason,
        counterfactual=counterfactual.to_dict(),
        config_version=config.version,
        state_snapshot=snapshot,
        evidence_ids=evidence_ids,
        created_at=datetime.now(timezone.utc).timestamp(),
    )


def replay_attempts(
    student_id: str,
    initial_states: Dict[str, MasteryState],
    attempts_with_questions: List[Tuple[Attempt, Question]],
    graph: CurriculumGraph,
    config: EngineConfig = DEFAULT_CONFIG,
) -> Tuple[Dict[str, MasteryState], List[Decision]]:
    """Deterministically re-executes knowledge tracing and decision generation
    over a stored sequence of attempts.
    Guarantees: Same evidence + Same config = Same decision.
    """
    states: Dict[str, MasteryState] = {
        k: MasteryState(**v.to_dict()) for k, v in initial_states.items()
    }
    decisions: List[Decision] = []
    attempts_history_by_item: Dict[str, Attempt] = {}

    for attempt, question in attempts_with_questions:
        c_id = question.concept_id
        if c_id not in states:
            states[c_id] = MasteryState(student_id=student_id, concept_id=c_id)

        # Check for temporal forgetting if gap exists
        curr_state = states[c_id]
        if attempt.created_at > curr_state.last_evidence_at:
            curr_state = apply_forgetting(curr_state, attempt.created_at, config)

        failure_mode = None
        if not attempt.correct:
            failure_mode, _ = classify_failure_mode(curr_state, attempt.created_at, config)

        prev_item_attempt = attempts_history_by_item.get(question.id)
        new_state, delta, log = update_mastery_bayesian(
            state=curr_state,
            question=question,
            attempt=attempt,
            previous_attempt_on_item=prev_item_attempt,
            config=config,
        )
        states[c_id] = new_state
        attempts_history_by_item[question.id] = attempt

        # Make decision after attempt
        dec = evaluate_and_record_decision(
            student_id=student_id,
            current_concept_id=c_id,
            student_states=states,
            graph=graph,
            evidence_ids=[attempt.id],
            config=config,
            last_failure_mode=failure_mode,
        )
        decisions.append(dec)

    return states, decisions
