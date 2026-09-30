"""Cold-start adaptive diagnostic endpoints."""

import json
import random
import time
import uuid
from typing import Dict, List, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User,
    ConceptRecord,
    ConceptPrereq,
    QuestionRecord,
    MasteryStateRecord,
    DecisionRecord,
    AuditLogRecord,
)
from app.schemas import (
    DiagnosticStartResponse,
    DiagnosticAnswerRequest,
    DiagnosticAnswerResponse,
    MasteryStateOut,
)
from app.auth import get_current_user
from engine.types import Concept as EngineConcept, MasteryState as EngineMasteryState
from engine.params import DEFAULT_CONFIG
from engine.graph import CurriculumGraph

router = APIRouter(prefix="/diagnostic", tags=["diagnostic"])

# 6 concepts covering root, middle, and leaf
DIAGNOSTIC_CONCEPTS = [
    "fraction_basics",      # Root 1
    "decimals_basics",      # Root 2
    "comparing_fractions",  # Middle 1
    "ratios",               # Middle 2
    "proportions",          # Leaf/Advanced 1
    "percentage_change",    # Leaf/Advanced 2
]

# In-memory session cache for active diagnostic flows: session_id -> session_data
_DIAGNOSTIC_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _get_curriculum_graph(db: Session) -> CurriculumGraph:
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


def _format_question(q: QuestionRecord) -> Dict[str, Any]:
    wrongs = json.loads(q.wrong_answers_json)
    options = [q.answer] + [w["option"] for w in wrongs]
    random.Random(q.id).shuffle(options)
    return {
        "id": q.id,
        "concept_id": q.concept_id,
        "difficulty": q.difficulty,
        "type": q.type,
        "prompt": q.prompt,
        "options": options,
    }


@router.post("/start", response_model=DiagnosticStartResponse)
def start_diagnostic(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Initializes 6-step cold start diagnostic session for student."""
    session_id = f"diag_{uuid.uuid4().hex[:12]}"
    first_concept_id = DIAGNOSTIC_CONCEPTS[0]

    q_db = (
        db.query(QuestionRecord)
        .filter(QuestionRecord.concept_id == first_concept_id)
        .first()
    )
    if not q_db:
        q_db = db.query(QuestionRecord).first()

    _DIAGNOSTIC_SESSIONS[session_id] = {
        "student_id": current_user.id,
        "step": 0,
        "results": {},  # concept_id -> (passed, score_p)
    }

    return DiagnosticStartResponse(
        session_id=session_id,
        total_steps=len(DIAGNOSTIC_CONCEPTS),
        current_step=1,
        question=_format_question(q_db),
    )


@router.post("/answer", response_model=DiagnosticAnswerResponse)
def answer_diagnostic(
    req: DiagnosticAnswerRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submits answer to current diagnostic probe. When all 6 are completed,
    runs bidirectional DAG propagation and persists all 10 concept states.
    """
    # Find matching question
    q_db = db.query(QuestionRecord).filter_by(id=req.question_id).first()
    if not q_db:
        raise HTTPException(status_code=404, detail="Question not found")

    correct = req.selected_answer.strip().lower() == q_db.answer.strip().lower()

    # Find or infer active session
    session = None
    for s_id, s_data in _DIAGNOSTIC_SESSIONS.items():
        if s_data["student_id"] == current_user.id:
            session = s_data
            break

    if not session:
        session = {
            "student_id": current_user.id,
            "step": 0,
            "results": {},
        }
        _DIAGNOSTIC_SESSIONS[f"diag_auto_{current_user.id}"] = session

    session["results"][req.concept_id] = (correct, 0.85 if correct else 0.25)
    session["step"] += 1
    curr_step = session["step"]
    total = len(DIAGNOSTIC_CONCEPTS)

    is_finished = curr_step >= total

    next_q_data = None
    inferred_states_out = None

    if not is_finished:
        next_concept_id = DIAGNOSTIC_CONCEPTS[curr_step]
        next_q_db = (
            db.query(QuestionRecord)
            .filter(QuestionRecord.concept_id == next_concept_id)
            .first()
        )
        if next_q_db:
            next_q_data = _format_question(next_q_db)
    else:
        # Diagnostic completed! Run graph propagation
        graph = _get_curriculum_graph(db)
        inferred = graph.infer_cold_start_states(
            student_id=current_user.id,
            diagnostic_evidence=session["results"],
            config=DEFAULT_CONFIG,
        )

        now = time.time()
        inferred_states_out = {}
        for c_id, st in inferred.items():
            rec = (
                db.query(MasteryStateRecord)
                .filter_by(student_id=current_user.id, concept_id=c_id)
                .first()
            )
            if not rec:
                rec = MasteryStateRecord(
                    student_id=current_user.id,
                    concept_id=c_id,
                    p_mastery=st.p_mastery,
                    uncertainty=st.uncertainty,
                    has_passed_transfer=st.has_passed_transfer,
                    highest_difficulty_passed=st.highest_difficulty_passed,
                    last_evidence_at=now,
                    status=st.status.value,
                    consecutive_failures=st.consecutive_failures,
                    total_attempts=st.total_attempts,
                    history_json=json.dumps(st.recent_history),
                )
                db.add(rec)
            else:
                rec.p_mastery = st.p_mastery
                rec.uncertainty = st.uncertainty
                rec.status = st.status.value
                rec.last_evidence_at = now

            inferred_states_out[c_id] = MasteryStateOut(
                student_id=current_user.id,
                concept_id=c_id,
                p_mastery=st.p_mastery,
                uncertainty=st.uncertainty,
                has_passed_transfer=st.has_passed_transfer,
                highest_difficulty_passed=st.highest_difficulty_passed,
                last_evidence_at=now,
                status=st.status.value,
                consecutive_failures=st.consecutive_failures,
                total_attempts=st.total_attempts,
                recent_history=st.recent_history,
            )

        # Record Audit
        db.add(
            AuditLogRecord(
                id=f"audit_diag_{uuid.uuid4().hex[:8]}",
                actor_id=current_user.id,
                event="DIAGNOSTIC_COMPLETED",
                payload_json=json.dumps({"diagnostic_results": {k: v[0] for k, v in session["results"].items()}}),
                created_at=now,
            )
        )
        db.commit()

    explanation = (
        f"Correct! Confirmed understanding on {req.concept_id}."
        if correct
        else f"Missed on {req.concept_id}. Correct answer: {q_db.answer}."
    )

    return DiagnosticAnswerResponse(
        step_completed=curr_step,
        total_steps=total,
        is_finished=is_finished,
        correct=correct,
        explanation=explanation,
        next_question=next_q_data,
        inferred_states=inferred_states_out,
    )
