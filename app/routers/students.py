"""Student learning flow, mastery state, attempts, recommendations, and agency."""

import json
import random
import time
import uuid
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User,
    ConceptRecord,
    ConceptPrereq,
    QuestionRecord,
    Attempt as AttemptModel,
    MasteryStateRecord,
    DecisionRecord,
    OverrideRecord,
    EngineConfigRecord,
    AuditLogRecord,
)
from app.schemas import (
    StudentStateResponse,
    MasteryStateOut,
    NextQuestionResponse,
    QuestionOut,
    AttemptSubmitRequest,
    AttemptSubmitResponse,
    RecommendationOut,
    CounterfactualOut,
    AgencyRequest,
    AgencyResponse,
)
from app.auth import get_current_user
from engine.types import (
    Action,
    Attempt as EngineAttempt,
    Concept as EngineConcept,
    Decision as EngineDecision,
    MasteryState as EngineMasteryState,
    MasteryStatus,
    Question as EngineQuestion,
    QuestionType,
)
from engine.params import EngineConfig, DEFAULT_CONFIG
from engine.graph import CurriculumGraph
from engine.bayesian import update_mastery_bayesian
from engine.forgetting import apply_forgetting, classify_failure_mode
from engine.recommender import determine_next_action, generate_counterfactual, evaluate_and_record_decision

router = APIRouter(tags=["students"])


def _load_engine_config(db: Session) -> EngineConfig:
    cfg_rec = db.query(EngineConfigRecord).order_by(EngineConfigRecord.created_at.desc()).first()
    if cfg_rec:
        try:
            return EngineConfig.from_dict(json.loads(cfg_rec.params_json))
        except Exception:
            pass
    return DEFAULT_CONFIG


def _load_curriculum_graph(db: Session) -> CurriculumGraph:
    concepts_db = db.query(ConceptRecord).all()
    edges_db = db.query(ConceptPrereq).all()
    prereqs_map: Dict[str, List[str]] = {c.id: [] for c in concepts_db}
    for e in edges_db:
        prereqs_map[e.concept_id].append(e.prereq_id)

    engine_concepts = [
        EngineConcept(
            id=c.id,
            name=c.name,
            description=c.description,
            domain=c.domain,
            tier=c.tier,
            prerequisites=prereqs_map.get(c.id, []),
        )
        for c in concepts_db
    ]
    return CurriculumGraph(engine_concepts)


def _load_student_states(student_id: str, db: Session) -> Dict[str, EngineMasteryState]:
    records = db.query(MasteryStateRecord).filter_by(student_id=student_id).all()
    states: Dict[str, EngineMasteryState] = {}
    for r in records:
        try:
            history = json.loads(r.history_json)
        except Exception:
            history = []
        states[r.concept_id] = EngineMasteryState(
            student_id=r.student_id,
            concept_id=r.concept_id,
            p_mastery=r.p_mastery,
            uncertainty=r.uncertainty,
            has_passed_transfer=r.has_passed_transfer,
            highest_difficulty_passed=r.highest_difficulty_passed,
            last_evidence_at=r.last_evidence_at,
            status=MasteryStatus(r.status),
            consecutive_failures=r.consecutive_failures,
            total_attempts=r.total_attempts,
            recent_history=history,
        )
    return states


@router.get("/students/{student_id}/state", response_model=StudentStateResponse)
def get_student_state(student_id: str, db: Session = Depends(get_db)):
    """Returns the student's mastery probabilities, uncertainty, and status across all concepts."""
    records = db.query(MasteryStateRecord).filter_by(student_id=student_id).all()
    concepts = {c.id: c.name for c in db.query(ConceptRecord).all()}

    states_out: Dict[str, MasteryStateOut] = {}
    for r in records:
        try:
            hist = json.loads(r.history_json)
        except Exception:
            hist = []
        states_out[r.concept_id] = MasteryStateOut(
            student_id=r.student_id,
            concept_id=r.concept_id,
            concept_name=concepts.get(r.concept_id, r.concept_id),
            p_mastery=r.p_mastery,
            uncertainty=r.uncertainty,
            has_passed_transfer=r.has_passed_transfer,
            highest_difficulty_passed=r.highest_difficulty_passed,
            last_evidence_at=r.last_evidence_at,
            status=r.status,
            consecutive_failures=r.consecutive_failures,
            total_attempts=r.total_attempts,
            recent_history=hist,
        )

    return StudentStateResponse(student_id=student_id, states=states_out)


@router.get("/students/{student_id}/recommendation", response_model=RecommendationOut)
def get_student_recommendation(student_id: str, db: Session = Depends(get_db)):
    """Computes explainable recommendation with reason and counterfactual formula."""
    config = _load_engine_config(db)
    graph = _load_curriculum_graph(db)
    states = _load_student_states(student_id, db)
    concepts = {c.id: c.name for c in db.query(ConceptRecord).all()}

    # Check if there is an active teacher override for the student's latest decision
    latest_dec = (
        db.query(DecisionRecord)
        .filter_by(student_id=student_id)
        .order_by(DecisionRecord.created_at.desc())
        .first()
    )

    if latest_dec and latest_dec.overrides:
        override = latest_dec.overrides[-1]
        target_name = concepts.get(override.new_target_concept_id, override.new_target_concept_id)
        try:
            cf_dict = json.loads(latest_dec.counterfactual_json)
        except Exception:
            cf_dict = {
                "current_action": override.new_action,
                "counterfactual_action": "Advance",
                "required_condition": "Complete teacher-assigned exercises",
                "target_concept_id": override.new_target_concept_id,
                "projected_mastery": 0.85,
                "explanation": "Teacher override in effect.",
            }

        return RecommendationOut(
            student_id=student_id,
            action=override.new_action,
            target_concept_id=override.new_target_concept_id,
            target_concept_name=target_name,
            reason=f"[Teacher Override by {override.teacher_id}]: {override.note}",
            counterfactual=CounterfactualOut(**cf_dict),
            config_version=latest_dec.config_version,
            created_at=override.created_at,
            is_override=True,
            override_note=override.note,
        )

    # Determine default concept to examine
    current_concept_id = "fraction_basics"
    if latest_dec:
        current_concept_id = latest_dec.target_concept_id

    action, target_id, reason = determine_next_action(
        current_concept_id=current_concept_id,
        student_states=states,
        graph=graph,
        config=config,
    )

    cf = generate_counterfactual(
        student_id=student_id,
        current_concept_id=current_concept_id,
        current_action=action,
        target_concept_id=target_id,
        student_states=states,
        graph=graph,
        config=config,
    )

    target_name = concepts.get(target_id, target_id)

    return RecommendationOut(
        student_id=student_id,
        action=action.value,
        target_concept_id=target_id,
        target_concept_name=target_name,
        reason=reason,
        counterfactual=CounterfactualOut(
            current_action=cf.current_action.value,
            counterfactual_action=cf.counterfactual_action.value,
            required_condition=cf.required_condition,
            target_concept_id=cf.target_concept_id,
            projected_mastery=cf.projected_mastery,
            explanation=cf.explanation,
        ),
        config_version=config.version,
        created_at=time.time(),
        is_override=False,
    )


@router.get("/students/{student_id}/next-question", response_model=NextQuestionResponse)
def get_next_question(student_id: str, db: Session = Depends(get_db)):
    """Selects the next optimal question tailored to the student's target concept and action."""
    rec = get_student_recommendation(student_id, db)
    target_concept_id = rec.target_concept_id
    action = rec.action

    # Choose difficulty and type based on action
    if action == Action.CHALLENGE.value:
        diff_target = 5
        type_target = "transfer"
    elif action == Action.ADVANCE.value:
        diff_target = 3
        type_target = "procedural"
    elif action == Action.INTERVENTION.value:
        diff_target = 1
        type_target = "recall"
    elif action == Action.REVIEW.value:
        diff_target = 2
        type_target = "recall"
    else:  # Practice / Remediate
        diff_target = 3
        type_target = "procedural"

    # Query matching questions from DB
    candidates = (
        db.query(QuestionRecord)
        .filter(QuestionRecord.concept_id == target_concept_id)
        .all()
    )

    if not candidates:
        # Fallback to any question
        candidates = db.query(QuestionRecord).all()

    # Prioritize items matching difficulty target
    best_q = None
    for q in candidates:
        if q.difficulty == diff_target and q.type == type_target:
            best_q = q
            break

    if not best_q:
        for q in candidates:
            if abs(q.difficulty - diff_target) <= 1:
                best_q = q
                break

    if not best_q and candidates:
        best_q = candidates[0]

    if not best_q:
        return NextQuestionResponse(
            question=None,
            recommendation=rec.dict(),
        )

    # Format options (correct answer + wrong options shuffled deterministically)
    wrong_answers = json.loads(best_q.wrong_answers_json)
    hints = json.loads(best_q.hints_json)
    options = [best_q.answer] + [w["option"] for w in wrong_answers]
    # Stable shuffle using question id
    random.Random(best_q.id).shuffle(options)

    concept_rec = db.query(ConceptRecord).filter_by(id=best_q.concept_id).first()
    concept_name = concept_rec.name if concept_rec else best_q.concept_id

    q_out = QuestionOut(
        id=best_q.id,
        concept_id=best_q.concept_id,
        concept_name=concept_name,
        difficulty=best_q.difficulty,
        type=best_q.type,
        prompt=best_q.prompt,
        options=options,
        hints=hints,
    )

    return NextQuestionResponse(
        question=q_out,
        recommendation=rec.model_dump(),
    )


@router.post("/attempts", response_model=AttemptSubmitResponse)
def submit_attempt(
    req: AttemptSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submits answer, evaluates Bayesian update with evidence weight,
    persists attempt and state, and updates recommendations.
    """
    student_id = current_user.id
    config = _load_engine_config(db)
    graph = _load_curriculum_graph(db)

    # 1. Fetch Question
    q_db = db.query(QuestionRecord).filter_by(id=req.question_id).first()
    if not q_db:
        raise HTTPException(status_code=404, detail="Question not found")

    correct = req.answer.strip().lower() == q_db.answer.strip().lower()

    # Identify misconception if incorrect
    misconception_text = None
    if not correct:
        wrongs = json.loads(q_db.wrong_answers_json)
        for w in wrongs:
            if w["option"].strip().lower() == req.answer.strip().lower():
                misconception_text = w.get("misconception")
                break

    # 2. Load prior state
    state_db = (
        db.query(MasteryStateRecord)
        .filter_by(student_id=student_id, concept_id=req.concept_id)
        .first()
    )
    now = time.time()

    if not state_db:
        engine_state = EngineMasteryState(
            student_id=student_id,
            concept_id=req.concept_id,
            last_evidence_at=now,
        )
    else:
        try:
            hist = json.loads(state_db.history_json)
        except Exception:
            hist = []
        engine_state = EngineMasteryState(
            student_id=student_id,
            concept_id=req.concept_id,
            p_mastery=state_db.p_mastery,
            uncertainty=state_db.uncertainty,
            has_passed_transfer=state_db.has_passed_transfer,
            highest_difficulty_passed=state_db.highest_difficulty_passed,
            last_evidence_at=state_db.last_evidence_at,
            status=MasteryStatus(state_db.status),
            consecutive_failures=state_db.consecutive_failures,
            total_attempts=state_db.total_attempts,
            recent_history=hist,
        )

    # 3. Check for temporal forgetting decay if elapsed gap exists
    if now > engine_state.last_evidence_at:
        engine_state = apply_forgetting(engine_state, now, config)

    failure_mode = None
    if not correct:
        failure_mode, _ = classify_failure_mode(engine_state, now, config)

    # 4. Check for previous attempt on same item (60s rapid retry rule)
    prev_att = (
        db.query(AttemptModel)
        .filter_by(student_id=student_id, question_id=req.question_id)
        .order_by(AttemptModel.created_at.desc())
        .first()
    )
    prev_engine_att = None
    if prev_att:
        prev_engine_att = EngineAttempt(
            id=prev_att.id,
            student_id=prev_att.student_id,
            question_id=prev_att.question_id,
            concept_id=prev_att.concept_id,
            correct=prev_att.correct,
            confidence=prev_att.confidence,
            response_ms=prev_att.response_ms,
            hints_used=prev_att.hints_used,
            attempt_no=prev_att.attempt_no,
            created_at=prev_att.created_at,
            weight_applied=prev_att.weight_applied,
        )

    # Construct Engine objects
    attempt_id = f"att_{uuid.uuid4().hex[:12]}"
    cur_engine_att = EngineAttempt(
        id=attempt_id,
        student_id=student_id,
        question_id=req.question_id,
        concept_id=req.concept_id,
        correct=correct,
        confidence=req.confidence,
        response_ms=req.response_ms,
        hints_used=req.hints_used,
        attempt_no=req.attempt_no,
        created_at=now,
    )

    q_engine = EngineQuestion(
        id=q_db.id,
        concept_id=q_db.concept_id,
        difficulty=q_db.difficulty,
        type=QuestionType(q_db.type),
        prompt=q_db.prompt,
        answer=q_db.answer,
    )

    # 5. Bayesian Knowledge Tracing calculation
    updated_state, delta_p, log_desc = update_mastery_bayesian(
        state=engine_state,
        question=q_engine,
        attempt=cur_engine_att,
        previous_attempt_on_item=prev_engine_att,
        config=config,
    )

    # 6. Save Attempt in DB
    db_att = AttemptModel(
        id=attempt_id,
        student_id=student_id,
        question_id=req.question_id,
        concept_id=req.concept_id,
        correct=correct,
        confidence=req.confidence,
        response_ms=req.response_ms,
        hints_used=req.hints_used,
        attempt_no=req.attempt_no,
        weight_applied=cur_engine_att.weight_applied,
        created_at=now,
    )
    db.add(db_att)

    # 7. Persist or Update Mastery State in DB
    if not state_db:
        state_db = MasteryStateRecord(
            student_id=student_id,
            concept_id=req.concept_id,
            p_mastery=updated_state.p_mastery,
            uncertainty=updated_state.uncertainty,
            has_passed_transfer=updated_state.has_passed_transfer,
            highest_difficulty_passed=updated_state.highest_difficulty_passed,
            last_evidence_at=now,
            status=updated_state.status.value,
            consecutive_failures=updated_state.consecutive_failures,
            total_attempts=updated_state.total_attempts,
            history_json=json.dumps(updated_state.recent_history),
        )
        db.add(state_db)
    else:
        state_db.p_mastery = updated_state.p_mastery
        state_db.uncertainty = updated_state.uncertainty
        state_db.has_passed_transfer = updated_state.has_passed_transfer
        state_db.highest_difficulty_passed = updated_state.highest_difficulty_passed
        state_db.last_evidence_at = now
        state_db.status = updated_state.status.value
        state_db.consecutive_failures = updated_state.consecutive_failures
        state_db.total_attempts = updated_state.total_attempts
        state_db.history_json = json.dumps(updated_state.recent_history)

    db.flush()

    # 8. Compute Decision & Counterfactual
    all_states = _load_student_states(student_id, db)
    all_states[req.concept_id] = updated_state

    dec = evaluate_and_record_decision(
        student_id=student_id,
        current_concept_id=req.concept_id,
        student_states=all_states,
        graph=graph,
        evidence_ids=[attempt_id],
        config=config,
        last_failure_mode=failure_mode,
    )

    db_dec = DecisionRecord(
        id=dec.id,
        student_id=student_id,
        action=dec.action.value,
        target_concept_id=dec.target_concept_id,
        reason=dec.reason,
        counterfactual_json=json.dumps(dec.counterfactual),
        config_version=dec.config_version,
        state_snapshot_json=json.dumps(dec.state_snapshot),
        evidence_ids_json=json.dumps(dec.evidence_ids),
        created_at=now,
    )
    db.add(db_dec)

    db.commit()

    feedback_str = (
        "Correct! Great work."
        if correct
        else f"Incorrect. The correct answer was: {q_db.answer}."
    )

    return AttemptSubmitResponse(
        correct=correct,
        weight_applied=cur_engine_att.weight_applied,
        feedback=feedback_str,
        misconception=misconception_text,
        new_mastery=updated_state.p_mastery,
        new_uncertainty=updated_state.uncertainty,
        delta_p=delta_p,
        status=updated_state.status.value,
        next_action=dec.action.value,
        target_concept_id=dec.target_concept_id,
        reason=dec.reason,
        counterfactual=dec.counterfactual,
    )


@router.post("/students/{student_id}/agency", response_model=AgencyResponse)
def exercise_student_agency(
    student_id: str,
    req: AgencyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Allows student to choose their own focus concept.
    Logs agency choice in audit_log and records manual decision diversion.
    """
    concept = db.query(ConceptRecord).filter_by(id=req.chosen_concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Selected concept not found")

    now = time.time()
    # Log in audit
    audit = AuditLogRecord(
        id=f"audit_agency_{uuid.uuid4().hex[:8]}",
        actor_id=student_id,
        event="STUDENT_AGENCY_CHOICE",
        payload_json=json.dumps({
            "chosen_concept_id": req.chosen_concept_id,
            "concept_name": concept.name,
            "student_note": req.reason,
        }),
        created_at=now,
    )
    db.add(audit)

    # Record decision acknowledging agency
    db_dec = DecisionRecord(
        id=str(uuid.uuid4()),
        student_id=student_id,
        action=Action.PRACTICE.value,
        target_concept_id=req.chosen_concept_id,
        reason=f"Student Agency: learner selected {concept.name} directly.",
        counterfactual_json=json.dumps({
            "current_action": Action.PRACTICE.value,
            "counterfactual_action": Action.ADVANCE.value,
            "required_condition": "Score >= 80% on this topic",
            "target_concept_id": req.chosen_concept_id,
            "projected_mastery": 0.85,
            "explanation": "Mastering this chosen concept will advance to child concepts.",
        }),
        config_version="v1.0.0",
        state_snapshot_json="{}",
        evidence_ids_json="[]",
        created_at=now,
    )
    db.add(db_dec)
    db.commit()

    return AgencyResponse(
        success=True,
        message=f"Focus updated to {concept.name} based on student agency choice.",
        target_concept_id=concept.id,
        target_concept_name=concept.name,
    )
