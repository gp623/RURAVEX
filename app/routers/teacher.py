"""Teacher analytics, class heatmap, stuck learners, overrides, and engine config."""

import json
import time
import uuid
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User,
    ConceptRecord,
    Attempt,
    MasteryStateRecord,
    DecisionRecord,
    OverrideRecord,
    EngineConfigRecord,
    AuditLogRecord,
)
from app.schemas import (
    ClassHeatmapItem,
    StuckStudentItem,
    StudentPathItem,
    TeacherOverrideRequest,
    ConfigUpdateRequest,
)
from app.auth import get_current_user, require_teacher

router = APIRouter(prefix="/teacher", tags=["teacher"])


@router.get("/class-heatmap", response_model=List[ClassHeatmapItem])
def get_class_heatmap(
    teacher: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Generates the Students x Concepts heatmap matrix with mastery values,
    uncertainties, and status color badges.
    """
    students = db.query(User).filter_by(role="student").all()
    concepts = db.query(ConceptRecord).order_by(ConceptRecord.tier).all()

    heatmap: List[ClassHeatmapItem] = []
    for s in students:
        states = db.query(MasteryStateRecord).filter_by(student_id=s.id).all()
        concept_dict = {
            r.concept_id: {
                "p_mastery": round(r.p_mastery, 3),
                "uncertainty": round(r.uncertainty, 3),
                "status": r.status,
                "has_passed_transfer": r.has_passed_transfer,
                "highest_difficulty_passed": r.highest_difficulty_passed,
            }
            for r in states
        }

        # Fill missing with default
        for c in concepts:
            if c.id not in concept_dict:
                concept_dict[c.id] = {
                    "p_mastery": 0.15,
                    "uncertainty": 0.85,
                    "status": "unencountered",
                    "has_passed_transfer": False,
                    "highest_difficulty_passed": 0,
                }

        # Calculate overall progress (% proficient or mastered)
        proficient_count = sum(
            1 for c_data in concept_dict.values() if c_data["p_mastery"] >= 0.80
        )
        progress = round((proficient_count / max(1, len(concepts))) * 100, 1)

        heatmap.append(
            ClassHeatmapItem(
                student_id=s.id,
                student_name=s.name,
                concepts=concept_dict,
                overall_progress=progress,
            )
        )

    return heatmap


@router.get("/stuck", response_model=List[StuckStudentItem])
def get_stuck_students(
    teacher: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Detects learners flagged for intervention (3+ consecutive failed cycles,
    struggling status, or persistent high uncertainty).
    """
    stuck_records = (
        db.query(MasteryStateRecord)
        .filter(
            (MasteryStateRecord.consecutive_failures >= 3)
            | (MasteryStateRecord.status == "struggling")
            | (
                (MasteryStateRecord.total_attempts >= 6)
                & (MasteryStateRecord.p_mastery < 0.40)
            )
        )
        .all()
    )

    concepts = {c.id: c.name for c in db.query(ConceptRecord).all()}
    students = {s.id: s.name for s in db.query(User).filter_by(role="student").all()}

    items: List[StuckStudentItem] = []
    for r in stuck_records:
        reason_parts = []
        if r.consecutive_failures >= 3:
            reason_parts.append(f"{r.consecutive_failures} consecutive failed attempts")
        if r.status == "struggling":
            reason_parts.append("Mastery state categorized as struggling")
        if r.total_attempts >= 6 and r.p_mastery < 0.40:
            reason_parts.append(f"Low mastery ({round(r.p_mastery * 100)}%) after {r.total_attempts} attempts")

        items.append(
            StuckStudentItem(
                student_id=r.student_id,
                student_name=students.get(r.student_id, r.student_id),
                concept_id=r.concept_id,
                concept_name=concepts.get(r.concept_id, r.concept_id),
                consecutive_failures=r.consecutive_failures,
                total_attempts=r.total_attempts,
                p_mastery=round(r.p_mastery, 3),
                uncertainty=round(r.uncertainty, 3),
                status=r.status,
                stuck_reason="; ".join(reason_parts) or "Algorithmic alert",
            )
        )

    return items


@router.get("/students/{student_id}/path", response_model=List[StudentPathItem])
def get_student_learning_path(
    student_id: str,
    teacher: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Returns chronological timeline of attempts, engine decisions, and teacher overrides."""
    events: List[StudentPathItem] = []

    # 1. Attempts
    attempts = db.query(Attempt).filter_by(student_id=student_id).all()
    for a in attempts:
        events.append(
            StudentPathItem(
                timestamp=a.created_at,
                event_type="attempt",
                details={
                    "attempt_id": a.id,
                    "concept_id": a.concept_id,
                    "question_id": a.question_id,
                    "correct": a.correct,
                    "confidence": a.confidence,
                    "response_ms": a.response_ms,
                    "hints_used": a.hints_used,
                    "weight_applied": a.weight_applied,
                },
            )
        )

    # 2. Decisions
    decisions = db.query(DecisionRecord).filter_by(student_id=student_id).all()
    for d in decisions:
        try:
            cf = json.loads(d.counterfactual_json)
        except Exception:
            cf = {}
        events.append(
            StudentPathItem(
                timestamp=d.created_at,
                event_type="decision",
                details={
                    "decision_id": d.id,
                    "action": d.action,
                    "target_concept_id": d.target_concept_id,
                    "reason": d.reason,
                    "counterfactual": cf,
                    "config_version": d.config_version,
                },
            )
        )

    # 3. Overrides
    overrides = db.query(OverrideRecord).filter_by(student_id=student_id).all()
    for o in overrides:
        events.append(
            StudentPathItem(
                timestamp=o.created_at,
                event_type="override",
                details={
                    "override_id": o.id,
                    "teacher_id": o.teacher_id,
                    "original_action": o.original_action,
                    "new_action": o.new_action,
                    "new_target_concept_id": o.new_target_concept_id,
                    "note": o.note,
                },
            )
        )

    # Sort chronologically
    events.sort(key=lambda x: x.timestamp)
    return events


@router.post("/overrides")
def create_teacher_override(
    req: TeacherOverrideRequest,
    teacher: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Enables teacher to override engine recommendation with mandatory justification note."""
    if len(req.note.strip()) < 6:
        raise HTTPException(
            status_code=400,
            detail="A substantive teacher note (at least 6 characters) is mandatory for overrides.",
        )

    decision = db.query(DecisionRecord).filter_by(id=req.decision_id).first()
    if not decision:
        decision = (
            db.query(DecisionRecord)
            .filter_by(student_id=req.student_id)
            .order_by(DecisionRecord.created_at.desc())
            .first()
        )
    if not decision:
        decision = DecisionRecord(
            id=f"dec_{uuid.uuid4().hex[:12]}",
            student_id=req.student_id,
            action="Teacher Intervention",
            target_concept_id=req.new_target_concept_id,
            reason="Algorithmic baseline prior to intervention.",
            counterfactual_json="{}",
            config_version="v1.0.0",
            state_snapshot_json="{}",
            evidence_ids_json="[]",
            created_at=time.time(),
        )
        db.add(decision)
        db.flush()

    target_concept = db.query(ConceptRecord).filter_by(id=req.new_target_concept_id).first()
    if not target_concept:
        raise HTTPException(status_code=404, detail="Target concept not found")

    now = time.time()
    override_id = f"ov_{uuid.uuid4().hex[:12]}"
    override = OverrideRecord(
        id=override_id,
        decision_id=decision.id,
        student_id=req.student_id,
        teacher_id=teacher.id,
        original_action=decision.action,
        new_action=req.new_action,
        new_target_concept_id=req.new_target_concept_id,
        note=req.note.strip(),
        created_at=now,
    )
    db.add(override)

    # Audit log
    audit = AuditLogRecord(
        id=f"audit_ov_{uuid.uuid4().hex[:8]}",
        actor_id=teacher.id,
        event="TEACHER_OVERRIDE_CREATED",
        payload_json=json.dumps({
            "decision_id": req.decision_id,
            "student_id": req.student_id,
            "from_action": decision.action,
            "to_action": req.new_action,
            "to_concept": req.new_target_concept_id,
            "note": req.note,
        }),
        created_at=now,
    )
    db.add(audit)
    db.commit()

    return {
        "success": True,
        "override_id": override_id,
        "message": f"Override applied: {req.new_action} on {target_concept.name}",
    }


@router.put("/config")
def update_engine_config(
    req: ConfigUpdateRequest,
    teacher: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Updates versioned engine hyperparameters with an audit trail."""
    now = time.time()
    # Generate new semantic version
    latest = db.query(EngineConfigRecord).order_by(EngineConfigRecord.created_at.desc()).first()
    last_ver = latest.version if latest else "v1.0.0"
    try:
        parts = last_ver.lstrip("v").split(".")
        new_ver = f"v{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    except Exception:
        new_ver = f"v1.0.{int(now) % 1000}"

    new_cfg = EngineConfigRecord(
        version=new_ver,
        params_json=json.dumps(req.params),
        created_at=now,
        created_by=teacher.id,
    )
    db.add(new_cfg)

    audit = AuditLogRecord(
        id=f"audit_cfg_{uuid.uuid4().hex[:8]}",
        actor_id=teacher.id,
        event="ENGINE_CONFIG_UPDATED",
        payload_json=json.dumps({
            "new_version": new_ver,
            "updated_by": teacher.id,
            "comment": req.comment,
            "params": req.params,
        }),
        created_at=now,
    )
    db.add(audit)
    db.commit()

    return {
        "success": True,
        "version": new_ver,
        "message": f"Engine config updated to version {new_ver}",
    }


@router.get("/audit")
def get_audit_logs(
    teacher: User = Depends(require_teacher),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Retrieves immutable audit trail of decisions, overrides, and config changes."""
    logs = (
        db.query(AuditLogRecord)
        .order_by(AuditLogRecord.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": l.id,
            "actor_id": l.actor_id,
            "event": l.event,
            "payload": json.loads(l.payload_json) if l.payload_json else {},
            "created_at": l.created_at,
        }
        for l in logs
    ]
