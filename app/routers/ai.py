"""Sandboxed AI endpoints for question generation and explanation rephrasing."""

import random
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ConceptRecord, User
from app.schemas import (
    AIGenerateQuestionRequest,
    AIGenerateQuestionResponse,
    AIExplainRequest,
    AIExplainResponse,
    QuestionOut,
)
from app.auth import get_current_user
from app.ai.client import generate_ai_question, rephrase_engine_explanation

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/generate-question", response_model=AIGenerateQuestionResponse)
async def api_generate_question(
    req: AIGenerateQuestionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generates a practice question with strict math verification or safe fallback."""
    concept = db.query(ConceptRecord).filter_by(id=req.concept_id).first()
    concept_name = concept.name if concept else req.concept_id

    q_data, ai_status, log = await generate_ai_question(
        concept_id=req.concept_id,
        concept_name=concept_name,
        difficulty=req.difficulty,
        q_type=req.type,
    )

    options = [q_data["answer"]] + [w["option"] for w in q_data["wrong_answers"]]
    random.Random(q_data["id"]).shuffle(options)

    q_out = QuestionOut(
        id=q_data["id"],
        concept_id=q_data["concept_id"],
        concept_name=concept_name,
        difficulty=q_data["difficulty"],
        type=q_data["type"],
        prompt=q_data["prompt"],
        options=options,
        hints=q_data["hints"],
    )

    return AIGenerateQuestionResponse(
        question=q_out,
        verified=True,
        ai_status=ai_status,
        validation_log=log,
    )


@router.post("/explain", response_model=AIExplainResponse)
async def api_explain_decision(
    req: AIExplainRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rephrases deterministic reason string while verifying all numbers are preserved."""
    student = db.query(User).filter_by(id=req.student_id).first()
    student_name = student.name if student else "Learner"

    friendly, status_out, preserved = await rephrase_engine_explanation(
        numeric_reason=req.numeric_reason,
        student_name=student_name,
    )

    return AIExplainResponse(
        friendly_explanation=friendly,
        ai_status=status_out,
        numbers_preserved=preserved,
    )
