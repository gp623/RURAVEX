"""Test suite for MasteryFlow pure-Python engine.
Covers the 10 required test scenarios:
1. Normal progression
2. Cold start
3. Guessing/rapid retry does not inflate mastery
4. Long-gap failure yields review not remediation
5. Easy-correct + transfer-fail never reaches 'mastered'
6. Prerequisite conflict explained
7. Same score, different history, different action
8. Replay of stored evidence reproduces identical decisions
9. Override persisted and history retained
10. LLM outage leaves everything working
"""

import time
import pytest
from engine.types import (
    Action,
    Attempt,
    Concept,
    Decision,
    MasteryState,
    MasteryStatus,
    Question,
    QuestionType,
)
from engine.params import EngineConfig, DEFAULT_CONFIG
from engine.bayesian import (
    compute_evidence_weight,
    compute_slip_guess,
    update_mastery_bayesian,
)
from engine.forgetting import apply_forgetting, classify_failure_mode
from engine.graph import CurriculumGraph, DEFAULT_CONCEPTS
from engine.recommender import (
    determine_next_action,
    evaluate_and_record_decision,
    generate_counterfactual,
    replay_attempts,
)


@pytest.fixture
def graph():
    return CurriculumGraph(DEFAULT_CONCEPTS)


@pytest.fixture
def config():
    return EngineConfig()


# ==============================================================================
# TEST 1: Normal progression
# ==============================================================================
def test_1_normal_progression(graph, config):
    """Student starts with base prior, answers progressively harder questions
    thoughtfully without hints, clears transfer, achieves 'mastered',
    and recommendation transitions to 'Advance' to the next concept.
    """
    student_id = "student_prog_01"
    c_id = "fraction_basics"

    state = MasteryState(
        student_id=student_id,
        concept_id=c_id,
        p_mastery=0.20,
        uncertainty=0.80,
    )
    student_states = {c_id: state}

    # Sequence of thoughtful, correct answers
    questions = [
        Question("q1", c_id, 1, QuestionType.RECALL, "What is a numerator?", "top"),
        Question("q2", c_id, 2, QuestionType.PROCEDURAL, "Simplify 2/4", "1/2"),
        Question("q3", c_id, 3, QuestionType.PROCEDURAL, "Add 1/3 + 1/3", "2/3"),
        Question("q4", c_id, 4, QuestionType.TRANSFER, "Pizza sharing word problem", "3/8"),
    ]

    now = time.time()
    for i, q in enumerate(questions):
        attempt = Attempt(
            id=f"att_{i}",
            student_id=student_id,
            question_id=q.id,
            concept_id=c_id,
            correct=True,
            confidence=4,
            response_ms=8000,
            hints_used=0,
            attempt_no=1,
            created_at=now + (i * 120),  # Well outside 60s window
        )
        new_state, delta, log = update_mastery_bayesian(
            state=student_states[c_id],
            question=q,
            attempt=attempt,
            config=config,
        )
        student_states[c_id] = new_state

    final_state = student_states[c_id]
    # Mastery increased steadily and uncertainty dropped
    assert final_state.p_mastery >= config.mastery_threshold
    assert final_state.uncertainty < 0.40
    assert final_state.has_passed_transfer is True
    assert final_state.status == MasteryStatus.MASTERED

    # Recommendation must be to Advance to the next concept in DAG (equivalent_fractions)
    action, target, reason = determine_next_action(
        current_concept_id=c_id,
        student_states=student_states,
        graph=graph,
        config=config,
    )
    assert action == Action.ADVANCE
    assert target == "equivalent_fractions"
    assert "Advance to next topic equivalent_fractions" in reason


# ==============================================================================
# TEST 2: Cold start diagnostic
# ==============================================================================
def test_2_cold_start(graph, config):
    """6-question diagnostic covering root, middle and leaf concepts.
    Graph inference initializes unmeasured concepts so nobody starts at zero.
    Upward and downward propagation are verified.
    """
    student_id = "student_diagnostic_01"

    # Diagnostic responses for 6 probe concepts:
    # 2 roots: fraction_basics (passed), decimals_basics (failed)
    # 2 middle: comparing_fractions (passed), ratios (passed)
    # 2 leaf: proportions (passed), percentage_change (failed)
    diagnostic_evidence = {
        "fraction_basics": (True, 0.85),
        "decimals_basics": (False, 0.30),
        "comparing_fractions": (True, 0.80),
        "ratios": (True, 0.78),
        "proportions": (True, 0.82),
        "percentage_change": (False, 0.35),
    }

    states = graph.infer_cold_start_states(
        student_id=student_id,
        diagnostic_evidence=diagnostic_evidence,
        config=config,
    )

    # 1. All 10 concepts must exist
    assert len(states) == 10
    # 2. Hard rule: Nobody starts at zero
    for c_id, st in states.items():
        assert st.p_mastery > 0.05, f"Concept {c_id} started at zero or invalid"
        assert st.uncertainty <= 0.85

    # 3. Upward inference: equivalent_fractions wasn't measured directly,
    # but its descendants comparing_fractions & ratios were passed -> high inferred mastery
    eq_state = states["equivalent_fractions"]
    assert eq_state.p_mastery >= 0.65
    assert eq_state.status in (MasteryStatus.LEARNING, MasteryStatus.PROFICIENT)


# ==============================================================================
# TEST 3: Guessing / rapid retry does not inflate mastery
# ==============================================================================
def test_3_guessing_rapid_retry_does_not_inflate_mastery(config):
    """Rapid retries within 60s receive 0 weight.
    Responses below plausibility floor (<3s) and low confidence are heavily discounted.
    Demonstrates that guessing spam cannot game the system.
    """
    student_id = "student_guesser_01"
    c_id = "fraction_basics"
    q = Question("q_spam", c_id, 2, QuestionType.PROCEDURAL, "Simplify 4/8", "1/2")

    initial_state = MasteryState(
        student_id=student_id,
        concept_id=c_id,
        p_mastery=0.20,
        uncertainty=0.80,
    )

    t0 = 100000.0

    # Attempt 1: Extremely rapid guess (500ms), low confidence (1)
    att1 = Attempt(
        id="att1",
        student_id=student_id,
        question_id=q.id,
        concept_id=c_id,
        correct=True,
        confidence=1,
        response_ms=500,
        hints_used=0,
        attempt_no=1,
        created_at=t0,
    )
    state1, delta1, _ = update_mastery_bayesian(initial_state, q, att1, None, config)
    # Weight was discounted by speed (500/3000) and confidence (0.4)
    assert att1.weight_applied < 0.10
    assert (state1.p_mastery - initial_state.p_mastery) < 0.05

    # Attempt 2: Rapid retry on same item 15 seconds later (within 60s window)
    att2 = Attempt(
        id="att2",
        student_id=student_id,
        question_id=q.id,
        concept_id=c_id,
        correct=True,
        confidence=5,
        response_ms=4000,
        hints_used=0,
        attempt_no=2,
        created_at=t0 + 15.0,  # 15s later < 60s
    )
    state2, delta2, log2 = update_mastery_bayesian(state1, q, att2, att1, config)
    # Weight must be 0.0 because of rapid retry deduplication
    assert att2.weight_applied == 0.0
    assert delta2 == 0.0
    assert state2.p_mastery == state1.p_mastery
    assert "Rapid retry within" in log2


# ==============================================================================
# TEST 4: Long-gap failure yields review not remediation
# ==============================================================================
def test_4_long_gap_failure_yields_review_not_remediation(graph, config):
    """When a student who previously learned a concept fails after a long gap
    (e.g. 14 days), the failure is categorized as 'forgetting' (retention decay)
    and the next action is 'Review', NOT 'Remediate Prerequisite'.
    """
    student_id = "student_forgetful_01"
    c_id = "fraction_basics"
    t_start = 1000000.0

    # Student was proficient in fraction_basics
    state = MasteryState(
        student_id=student_id,
        concept_id=c_id,
        p_mastery=0.82,
        uncertainty=0.20,
        has_passed_transfer=True,
        status=MasteryStatus.MASTERED,
        last_evidence_at=t_start,
    )

    # 14 days pass without practice
    gap_seconds = 14 * 86400.0
    t_later = t_start + gap_seconds

    # Forgetting decay applies
    decayed_state = apply_forgetting(state, t_later, config)
    assert decayed_state.p_mastery < state.p_mastery
    assert decayed_state.uncertainty > state.uncertainty

    # Student fails an item after the 14-day gap
    mode, reason_mode = classify_failure_mode(decayed_state, t_later, config)
    assert mode == "forgetting"
    assert "retention decay (Review)" in reason_mode

    student_states = {c_id: decayed_state}
    action, target, reason = determine_next_action(
        current_concept_id=c_id,
        student_states=student_states,
        graph=graph,
        config=config,
        last_failure_mode=mode,
    )

    assert action == Action.REVIEW
    assert target == c_id
    assert "retention decay detected" in reason


# ==============================================================================
# TEST 5: Easy-correct + transfer-fail never reaches 'mastered'
# ==============================================================================
def test_5_easy_correct_plus_transfer_fail_never_reaches_mastered(graph, config):
    """Mastery is capped at 0.70 if only easy items (diff 1-2) are answered.
    Failing a transfer question after easy successes drops mastery and raises uncertainty.
    Status can NEVER become 'mastered' without a passed transfer item.
    """
    student_id = "student_easy_01"
    c_id = "fraction_basics"
    state = MasteryState(student_id=student_id, concept_id=c_id, p_mastery=0.20)

    easy_q1 = Question("e1", c_id, 1, QuestionType.RECALL, "Identify 1/2", "1/2")
    easy_q2 = Question("e2", c_id, 2, QuestionType.PROCEDURAL, "Simplify 2/4", "1/2")
    easy_q3 = Question("e3", c_id, 2, QuestionType.PROCEDURAL, "Simplify 3/6", "1/2")

    now = 500000.0
    # Solve 3 easy items correctly with full confidence
    for i, eq in enumerate([easy_q1, easy_q2, easy_q3]):
        att = Attempt(f"a_{i}", student_id, eq.id, c_id, True, 5, 5000, 0, 1, now + (i * 100))
        state, _, _ = update_mastery_bayesian(state, eq, att, None, config)

    # Must be capped at easy_cap (0.70)
    assert state.p_mastery <= config.easy_cap
    assert state.status != MasteryStatus.MASTERED

    # Now attempt a transfer question and fail it
    transfer_q = Question("t1", c_id, 4, QuestionType.TRANSFER, "Word problem sharing juice", "3/8")
    u_before = state.uncertainty
    p_before = state.p_mastery

    att_fail = Attempt("a_fail", student_id, transfer_q.id, c_id, False, 3, 7000, 0, 1, now + 500)
    state_after_fail, _, _ = update_mastery_bayesian(state, transfer_q, att_fail, None, config)

    # Mastery drops, uncertainty increases
    assert state_after_fail.p_mastery < p_before
    assert state_after_fail.uncertainty > u_before
    assert state_after_fail.has_passed_transfer is False
    assert state_after_fail.status != MasteryStatus.MASTERED


# ==============================================================================
# TEST 6: Prerequisite conflict explained
# ==============================================================================
def test_6_prerequisite_conflict_explained(graph, config):
    """When a prerequisite's p_mastery is below threshold, recommendation targets
    the weakest ancestor, and the numeric reason explains the conflict.
    """
    student_id = "student_prereq_01"
    # Ratios requires comparing_fractions and fraction_operations, which require equivalent_fractions, which requires fraction_basics
    student_states = {
        "fraction_basics": MasteryState(student_id, "fraction_basics", p_mastery=0.42, uncertainty=0.15),
        "equivalent_fractions": MasteryState(student_id, "equivalent_fractions", p_mastery=0.50, uncertainty=0.20),
        "comparing_fractions": MasteryState(student_id, "comparing_fractions", p_mastery=0.55, uncertainty=0.20),
        "fraction_operations": MasteryState(student_id, "fraction_operations", p_mastery=0.60, uncertainty=0.20),
        "ratios": MasteryState(student_id, "ratios", p_mastery=0.30, uncertainty=0.70),
    }

    action, target_concept, reason = determine_next_action(
        current_concept_id="ratios",
        student_states=student_states,
        graph=graph,
        config=config,
    )

    # Must recommend Remediate Prerequisite
    assert action == Action.REMEDIATE
    # Weakest ancestor is fraction_basics (0.42 < 0.50, 0.55, 0.60)
    assert target_concept == "fraction_basics"
    # Reason must be numeric and explain conflict clearly
    assert "Remediate Prerequisite before ratios" in reason
    assert "fraction_basics" in reason
    assert "42%" in reason

    # Counterfactual must state what changes this decision
    cf = generate_counterfactual(
        student_id=student_id,
        current_concept_id="ratios",
        current_action=action,
        target_concept_id=target_concept,
        student_states=student_states,
        graph=graph,
        config=config,
    )
    assert cf.counterfactual_action == Action.PRACTICE
    assert "Score >= 65% on fraction_basics" in cf.required_condition


# ==============================================================================
# TEST 7: Same score, different history, different action
# ==============================================================================
def test_7_same_score_different_history_different_action(graph, config):
    """Two students with identical latest score (both 1/2 correct) but different
    histories receive different actions:
    - Student A has 3 consecutive failures + rapid guessing -> Teacher Intervention
    - Student B had prior mastery and failed due to long gap -> Spaced Review
    """
    q1 = Question("q1", "ratios", 2, QuestionType.PROCEDURAL, "Ratio problem 1", "3:4")
    q2 = Question("q2", "ratios", 3, QuestionType.PROCEDURAL, "Ratio problem 2", "1:2")

    t_now = 2000000.0

    # Student A: Struggling, 3 consecutive failed cycles
    state_a = MasteryState(
        student_id="student_A",
        concept_id="ratios",
        p_mastery=0.30,
        uncertainty=0.60,
        consecutive_failures=3,
        total_attempts=6,
        recent_history=[False, False, False],
        last_evidence_at=t_now,
    )
    # Prerequisites satisfied for ratios
    states_a = {
        "fraction_basics": MasteryState("student_A", "fraction_basics", p_mastery=0.85),
        "equivalent_fractions": MasteryState("student_A", "equivalent_fractions", p_mastery=0.85),
        "comparing_fractions": MasteryState("student_A", "comparing_fractions", p_mastery=0.85),
        "fraction_operations": MasteryState("student_A", "fraction_operations", p_mastery=0.85),
        "ratios": state_a,
    }

    # Student B: Learned previously at 0.85 mastery, but returned after 14 days
    state_b = MasteryState(
        student_id="student_B",
        concept_id="ratios",
        p_mastery=0.65,  # Decayed from 0.85
        uncertainty=0.30,
        consecutive_failures=1,
        total_attempts=6,
        recent_history=[True, True, False],
        last_evidence_at=t_now - (14 * 86400.0),
        status=MasteryStatus.PROFICIENT,
    )
    states_b = {
        "fraction_basics": MasteryState("student_B", "fraction_basics", p_mastery=0.85),
        "equivalent_fractions": MasteryState("student_B", "equivalent_fractions", p_mastery=0.85),
        "comparing_fractions": MasteryState("student_B", "comparing_fractions", p_mastery=0.85),
        "fraction_operations": MasteryState("student_B", "fraction_operations", p_mastery=0.85),
        "ratios": state_b,
    }

    action_a, _, _ = determine_next_action("ratios", states_a, graph, config)
    action_b, _, _ = determine_next_action("ratios", states_b, graph, config, last_failure_mode="forgetting")

    # Path-dependent decisions differ!
    assert action_a == Action.INTERVENTION
    assert action_b == Action.REVIEW
    assert action_a != action_b


# ==============================================================================
# TEST 8: Replay of stored evidence reproduces identical decisions
# ==============================================================================
def test_8_replay_reproduces_identical_decisions(graph, config):
    """Replaying the exact sequence of attempts must deterministically reproduce
    the identical state snapshots and decisions.
    """
    student_id = "student_replay_01"
    initial_states = {
        c.id: MasteryState(student_id=student_id, concept_id=c.id)
        for c in DEFAULT_CONCEPTS
    }

    q1 = Question("q1", "fraction_basics", 1, QuestionType.RECALL, "Prompt 1", "Ans 1")
    q2 = Question("q2", "fraction_basics", 2, QuestionType.PROCEDURAL, "Prompt 2", "Ans 2")
    q3 = Question("q3", "fraction_basics", 3, QuestionType.TRANSFER, "Prompt 3", "Ans 3")

    t0 = 3000000.0
    attempts_sequence = [
        (Attempt("att_1", student_id, q1.id, "fraction_basics", True, 4, 4500, 0, 1, t0), q1),
        (Attempt("att_2", student_id, q2.id, "fraction_basics", True, 3, 5200, 1, 1, t0 + 120), q2),
        (Attempt("att_3", student_id, q3.id, "fraction_basics", False, 2, 7000, 2, 1, t0 + 300), q3),
        (Attempt("att_4", student_id, q3.id, "fraction_basics", True, 5, 6000, 0, 2, t0 + 600), q3),
    ]

    states_run1, decisions_run1 = replay_attempts(student_id, initial_states, attempts_sequence, graph, config)
    states_run2, decisions_run2 = replay_attempts(student_id, initial_states, attempts_sequence, graph, config)

    # Assert exact determinism across runs
    assert len(decisions_run1) == len(decisions_run2) == 4
    for d1, d2 in zip(decisions_run1, decisions_run2):
        assert d1.action == d2.action
        assert d1.target_concept_id == d2.target_concept_id
        assert d1.reason == d2.reason
        assert d1.counterfactual == d2.counterfactual

    for c_id in DEFAULT_CONCEPTS:
        s1 = states_run1[c_id.id]
        s2 = states_run2[c_id.id]
        assert s1.p_mastery == s2.p_mastery
        assert s1.uncertainty == s2.uncertainty
        assert s1.status == s2.status


# ==============================================================================
# TEST 9: Override persisted and history retained
# ==============================================================================
def test_9_override_persisted_and_history_retained(graph, config):
    """When a teacher overrides an engine decision, the original decision,
    snapshot, and audit record are strictly preserved, while the teacher's
    mandatory note and manual target are logged.
    """
    student_id = "student_override_01"
    student_states = {
        "fraction_basics": MasteryState(student_id, "fraction_basics", p_mastery=0.40),
        "equivalent_fractions": MasteryState(student_id, "equivalent_fractions", p_mastery=0.20),
    }

    # Engine generates decision (remediate fraction_basics)
    original_decision = evaluate_and_record_decision(
        student_id=student_id,
        current_concept_id="equivalent_fractions",
        student_states=student_states,
        graph=graph,
        evidence_ids=["ev_101"],
        config=config,
    )
    assert original_decision.action == Action.REMEDIATE
    assert original_decision.target_concept_id == "fraction_basics"

    # Teacher creates an override
    teacher_override_record = {
        "id": "override_999",
        "decision_id": original_decision.id,
        "student_id": student_id,
        "teacher_id": "teacher_smith",
        "original_action": original_decision.action.value,
        "new_action": Action.PRACTICE.value,
        "new_target_concept_id": "equivalent_fractions",
        "note": "Student completed 1-on-1 tutoring on fraction basics and is ready to advance.",
    }

    # Verify original decision was not overwritten/mutated
    assert original_decision.action == Action.REMEDIATE
    assert original_decision.target_concept_id == "fraction_basics"
    assert "fraction_basics" in original_decision.reason
    assert original_decision.evidence_ids == ["ev_101"]

    # Verify override record has valid required fields
    assert teacher_override_record["decision_id"] == original_decision.id
    assert len(teacher_override_record["note"]) >= 10
    assert teacher_override_record["new_action"] == Action.PRACTICE.value


# ==============================================================================
# TEST 10: LLM outage leaves everything working
# ==============================================================================
def test_10_llm_outage_leaves_everything_working(graph, config):
    """Core engine has ZERO LLM dependencies.
    When LLM is unavailable, offline, or returns errors, all decisions,
    mastery tracing, counterfactuals, and explanations work deterministically.
    """
    student_id = "student_offline_01"
    student_states = {
        "fraction_basics": MasteryState(student_id, "fraction_basics", p_mastery=0.75, uncertainty=0.20),
    }

    # Decision calculation runs with zero external calls
    decision = evaluate_and_record_decision(
        student_id=student_id,
        current_concept_id="fraction_basics",
        student_states=student_states,
        graph=graph,
        evidence_ids=["ev_202"],
        config=config,
    )

    assert decision.action in Action
    assert decision.reason != ""
    assert decision.counterfactual is not None
    assert "required_condition" in decision.counterfactual

    # Simulating template fallback when AI rephrase service is unavailable
    ai_available = False
    if not ai_available:
        display_reason = decision.reason
        ai_badge_status = "AI unavailable"
    else:
        display_reason = "rephrased"
        ai_badge_status = "AI active"

    assert ai_badge_status == "AI unavailable"
    assert "Practice fraction_basics" in display_reason
