"""Database seeding script.
Creates tables, populates concepts, prerequisites, 50 questions, initial config,
and realistic student cohorts (balanced, struggling, guessing, advanced, spaced decay).
"""

import json
import time
from sqlalchemy.orm import Session
from app.database import engine, SessionLocal, Base
from app.models import (
    User,
    ConceptRecord,
    ConceptPrereq,
    QuestionRecord,
    Attempt,
    MasteryStateRecord,
    DecisionRecord,
    OverrideRecord,
    EngineConfigRecord,
    AuditLogRecord,
)
from app.seed_data import SEED_CONCEPTS, SEED_QUESTIONS
from engine.params import DEFAULT_CONFIG
from engine.types import Action, MasteryStatus


import sys

def seed_database(drop_existing: bool = False):
    """Initializes schema and seeds realistic data."""
    if drop_existing:
        print("Dropping existing tables for clean seed...")
        Base.metadata.drop_all(bind=engine)

    print("Initializing database schema...")
    Base.metadata.create_all(bind=engine)

    db: Session = SessionLocal()
    try:
        # 1. Engine Config
        print("Seeding engine config (v1.0.0)...")
        existing_cfg = db.query(EngineConfigRecord).filter_by(version=DEFAULT_CONFIG.version).first()
        if not existing_cfg:
            cfg_record = EngineConfigRecord(
                version=DEFAULT_CONFIG.version,
                params_json=json.dumps(DEFAULT_CONFIG.to_dict()),
                created_at=time.time(),
                created_by="system_init",
            )
            db.add(cfg_record)

        # 2. Concepts & Prerequisites
        print("Seeding 10 curriculum concepts & DAG prerequisites...")
        for c_data in SEED_CONCEPTS:
            concept = db.query(ConceptRecord).filter_by(id=c_data["id"]).first()
            if not concept:
                concept = ConceptRecord(
                    id=c_data["id"],
                    name=c_data["name"],
                    description=c_data["description"],
                    domain=c_data["domain"],
                    tier=c_data["tier"],
                )
                db.add(concept)

        db.flush()

        for c_data in SEED_CONCEPTS:
            for p_id in c_data["prerequisites"]:
                edge = (
                    db.query(ConceptPrereq)
                    .filter_by(concept_id=c_data["id"], prereq_id=p_id)
                    .first()
                )
                if not edge:
                    db.add(ConceptPrereq(concept_id=c_data["id"], prereq_id=p_id))

        # 3. 50 Rich Questions
        print("Seeding 50 pedagogical questions...")
        for q_data in SEED_QUESTIONS:
            q = db.query(QuestionRecord).filter_by(id=q_data["id"]).first()
            if not q:
                q = QuestionRecord(
                    id=q_data["id"],
                    concept_id=q_data["concept_id"],
                    difficulty=q_data["difficulty"],
                    type=q_data["type"],
                    prompt=q_data["prompt"],
                    answer=q_data["answer"],
                    wrong_answers_json=json.dumps(q_data["wrong_answers"]),
                    hints_json=json.dumps(q_data["hints"]),
                    created_at=time.time(),
                )
                db.add(q)

        # 4. Standard Users (1 Teacher, 5 Archetype Students)
        print("Seeding teacher and student cohort...")
        users = [
            ("teacher_sarah", "Ms. Sarah Jenkins (Math Lead)", "teacher"),
            ("student_maya", "Maya Lin (Balanced Learner)", "student"),
            ("student_leo", "Leo Martinez (Struggling / Stuck)", "student"),
            ("student_alex", "Alex Chen (Rapid Guesser)", "student"),
            ("student_elena", "Elena Rostova (Advanced / Mastered)", "student"),
            ("student_sam", "Sam Taylor (Retention Decay / Review)", "student"),
        ]
        for u_id, name, role in users:
            if not db.query(User).filter_by(id=u_id).first():
                db.add(User(id=u_id, name=name, role=role, created_at=time.time()))

        db.flush()

        now = time.time()

        # 5. Populate Mastery States & Cohort Histories
        print("Seeding realistic attempt histories and mastery states...")

        # Student Elena: Advanced (mastered fractions, operations, ratios, proportions)
        elena_concepts = {
            "fraction_basics": (0.94, 0.10, True, 5, MasteryStatus.MASTERED.value),
            "equivalent_fractions": (0.92, 0.12, True, 5, MasteryStatus.MASTERED.value),
            "comparing_fractions": (0.90, 0.12, True, 4, MasteryStatus.MASTERED.value),
            "fraction_operations": (0.88, 0.15, True, 5, MasteryStatus.MASTERED.value),
            "decimals_basics": (0.91, 0.12, True, 4, MasteryStatus.MASTERED.value),
            "ratios": (0.89, 0.14, True, 5, MasteryStatus.MASTERED.value),
            "unit_rates": (0.87, 0.16, True, 4, MasteryStatus.MASTERED.value),
            "proportions": (0.85, 0.18, True, 4, MasteryStatus.MASTERED.value),
            "percentages": (0.68, 0.32, False, 2, MasteryStatus.LEARNING.value),
            "percentage_change": (0.25, 0.65, False, 0, MasteryStatus.UNENCOUNTERED.value),
        }
        for c_id, (p, u, tr, hd, st) in elena_concepts.items():
            if not db.query(MasteryStateRecord).filter_by(student_id="student_elena", concept_id=c_id).first():
                db.add(
                    MasteryStateRecord(
                        student_id="student_elena",
                        concept_id=c_id,
                        p_mastery=p,
                        uncertainty=u,
                        has_passed_transfer=tr,
                        highest_difficulty_passed=hd,
                        last_evidence_at=now - 3600,
                        status=st,
                        consecutive_failures=0,
                        total_attempts=8,
                        history_json=json.dumps([True, True, True, True, True]),
                    )
                )

        # Student Leo: Stuck on ratios (3 consecutive failures, triggers Teacher Intervention)
        leo_concepts = {
            "fraction_basics": (0.78, 0.22, True, 3, MasteryStatus.PROFICIENT.value),
            "equivalent_fractions": (0.72, 0.25, True, 3, MasteryStatus.LEARNING.value),
            "comparing_fractions": (0.68, 0.28, False, 2, MasteryStatus.LEARNING.value),
            "fraction_operations": (0.66, 0.30, False, 2, MasteryStatus.LEARNING.value),
            "decimals_basics": (0.45, 0.45, False, 1, MasteryStatus.LEARNING.value),
            "ratios": (0.28, 0.58, False, 1, MasteryStatus.STRUGGLING.value),
            "unit_rates": (0.20, 0.70, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "proportions": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "percentages": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "percentage_change": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
        }
        for c_id, (p, u, tr, hd, st) in leo_concepts.items():
            if not db.query(MasteryStateRecord).filter_by(student_id="student_leo", concept_id=c_id).first():
                db.add(
                    MasteryStateRecord(
                        student_id="student_leo",
                        concept_id=c_id,
                        p_mastery=p,
                        uncertainty=u,
                        has_passed_transfer=tr,
                        highest_difficulty_passed=hd,
                        last_evidence_at=now - 1200,
                        status=st,
                        consecutive_failures=3 if c_id == "ratios" else 0,
                        total_attempts=7 if c_id == "ratios" else 3,
                        history_json=json.dumps([True, False, False, False] if c_id == "ratios" else [True, True]),
                    )
                )

        # Student Sam: Learned fractions 14 days ago, returned with retention decay (triggers Review)
        t_14_days_ago = now - (14 * 86400)
        sam_concepts = {
            "fraction_basics": (0.58, 0.48, True, 4, MasteryStatus.PROFICIENT.value),  # Decayed from 0.85
            "equivalent_fractions": (0.52, 0.52, True, 3, MasteryStatus.LEARNING.value),
            "comparing_fractions": (0.20, 0.75, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "fraction_operations": (0.20, 0.75, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "decimals_basics": (0.30, 0.65, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "ratios": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "unit_rates": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "proportions": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "percentages": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "percentage_change": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
        }
        for c_id, (p, u, tr, hd, st) in sam_concepts.items():
            if not db.query(MasteryStateRecord).filter_by(student_id="student_sam", concept_id=c_id).first():
                db.add(
                    MasteryStateRecord(
                        student_id="student_sam",
                        concept_id=c_id,
                        p_mastery=p,
                        uncertainty=u,
                        has_passed_transfer=tr,
                        highest_difficulty_passed=hd,
                        last_evidence_at=t_14_days_ago,
                        status=st,
                        consecutive_failures=1 if c_id == "fraction_basics" else 0,
                        total_attempts=6 if c_id == "fraction_basics" else 0,
                        history_json=json.dumps([True, True, True, False] if c_id == "fraction_basics" else []),
                    )
                )

        # Student Maya: Balanced progressive learner
        maya_concepts = {
            "fraction_basics": (0.84, 0.20, True, 4, MasteryStatus.MASTERED.value),
            "equivalent_fractions": (0.76, 0.28, False, 3, MasteryStatus.LEARNING.value),
            "comparing_fractions": (0.50, 0.40, False, 2, MasteryStatus.LEARNING.value),
            "fraction_operations": (0.45, 0.45, False, 2, MasteryStatus.LEARNING.value),
            "decimals_basics": (0.70, 0.30, False, 2, MasteryStatus.LEARNING.value),
            "ratios": (0.25, 0.65, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "unit_rates": (0.20, 0.75, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "proportions": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "percentages": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
            "percentage_change": (0.15, 0.85, False, 0, MasteryStatus.UNENCOUNTERED.value),
        }
        for c_id, (p, u, tr, hd, st) in maya_concepts.items():
            if not db.query(MasteryStateRecord).filter_by(student_id="student_maya", concept_id=c_id).first():
                db.add(
                    MasteryStateRecord(
                        student_id="student_maya",
                        concept_id=c_id,
                        p_mastery=p,
                        uncertainty=u,
                        has_passed_transfer=tr,
                        highest_difficulty_passed=hd,
                        last_evidence_at=now - 1800,
                        status=st,
                        consecutive_failures=0,
                        total_attempts=5 if c_id == "fraction_basics" else 2,
                        history_json=json.dumps([True, True, True]),
                    )
                )

        # Seed realistic attempt histories
        attempts_to_seed = [
            # Leo: fraction success followed by 3 ratio failures
            Attempt(
                id="att_leo_01", student_id="student_leo", question_id="fb_1", concept_id="fraction_basics",
                correct=True, confidence=4, response_ms=5200, hints_used=0, attempt_no=1, weight_applied=1.0, created_at=now - 7200
            ),
            Attempt(
                id="att_leo_02", student_id="student_leo", question_id="fb_2", concept_id="fraction_basics",
                correct=True, confidence=4, response_ms=6400, hints_used=0, attempt_no=1, weight_applied=1.0, created_at=now - 6800
            ),
            Attempt(
                id="att_leo_03", student_id="student_leo", question_id="r_1", concept_id="ratios",
                correct=False, confidence=3, response_ms=8100, hints_used=1, attempt_no=1, weight_applied=0.7, created_at=now - 3600
            ),
            Attempt(
                id="att_leo_04", student_id="student_leo", question_id="r_2", concept_id="ratios",
                correct=False, confidence=2, response_ms=9500, hints_used=2, attempt_no=1, weight_applied=0.45, created_at=now - 2400
            ),
            Attempt(
                id="att_leo_05", student_id="student_leo", question_id="r_3", concept_id="ratios",
                correct=False, confidence=2, response_ms=7800, hints_used=3, attempt_no=1, weight_applied=0.15, created_at=now - 1200
            ),
            # Maya: successful balanced progression
            Attempt(
                id="att_maya_01", student_id="student_maya", question_id="fb_1", concept_id="fraction_basics",
                correct=True, confidence=5, response_ms=4200, hints_used=0, attempt_no=1, weight_applied=1.0, created_at=now - 3600
            ),
            Attempt(
                id="att_maya_02", student_id="student_maya", question_id="fb_2", concept_id="fraction_basics",
                correct=True, confidence=4, response_ms=5100, hints_used=0, attempt_no=1, weight_applied=1.0, created_at=now - 3000
            ),
            Attempt(
                id="att_maya_03", student_id="student_maya", question_id="fb_4", concept_id="fraction_basics",
                correct=True, confidence=4, response_ms=6200, hints_used=0, attempt_no=1, weight_applied=1.0, created_at=now - 2400
            ),
            # Alex: rapid guessing attempts
            Attempt(
                id="att_alex_01", student_id="student_alex", question_id="fb_1", concept_id="fraction_basics",
                correct=True, confidence=1, response_ms=1100, hints_used=0, attempt_no=1, weight_applied=0.15, created_at=now - 1800
            ),
            Attempt(
                id="att_alex_02", student_id="student_alex", question_id="fb_2", concept_id="fraction_basics",
                correct=True, confidence=1, response_ms=900, hints_used=0, attempt_no=1, weight_applied=0.15, created_at=now - 1790
            ),
            # Sam: attempts from 14 days ago
            Attempt(
                id="att_sam_01", student_id="student_sam", question_id="fb_1", concept_id="fraction_basics",
                correct=True, confidence=5, response_ms=3800, hints_used=0, attempt_no=1, weight_applied=1.0, created_at=t_14_days_ago - 7200
            ),
            Attempt(
                id="att_sam_02", student_id="student_sam", question_id="fb_2", concept_id="fraction_basics",
                correct=True, confidence=4, response_ms=4500, hints_used=0, attempt_no=1, weight_applied=1.0, created_at=t_14_days_ago - 3600
            ),
            Attempt(
                id="att_sam_03", student_id="student_sam", question_id="fb_4", concept_id="fraction_basics",
                correct=False, confidence=2, response_ms=9200, hints_used=1, attempt_no=1, weight_applied=0.7, created_at=t_14_days_ago
            ),
        ]
        for a in attempts_to_seed:
            if not db.query(Attempt).filter_by(id=a.id).first():
                db.add(a)

        # Initial Decision & Override for Leo
        dec_leo_id = "dec_leo_initial_01"
        if not db.query(DecisionRecord).filter_by(id=dec_leo_id).first():
            db.add(
                DecisionRecord(
                    id=dec_leo_id,
                    student_id="student_leo",
                    action=Action.INTERVENTION.value,
                    target_concept_id="ratios",
                    reason="Teacher Intervention required: 3 consecutive failed cycles on ratios. Mastery dropped to 28% (±58%).",
                    counterfactual_json=json.dumps({
                        "current_action": Action.INTERVENTION.value,
                        "counterfactual_action": Action.PRACTICE.value,
                        "required_condition": "1 guided attempt with teacher reset or 1 correct answer without hints",
                        "target_concept_id": "ratios",
                        "projected_mastery": 0.38,
                        "explanation": "A single unprompted correct response will clear the consecutive error counter.",
                    }),
                    config_version=DEFAULT_CONFIG.version,
                    state_snapshot_json=json.dumps({"ratios": {"p_mastery": 0.28, "uncertainty": 0.58}}),
                    evidence_ids_json=json.dumps(["att_leo_01", "att_leo_02", "att_leo_03"]),
                    created_at=now - 600,
                )
            )

        # Audit Log Entry
        db.add(
            AuditLogRecord(
                id=f"audit_seed_{int(now)}",
                actor_id="system",
                event="SYSTEM_SEED_COMPLETED",
                payload_json=json.dumps({"concepts": 10, "questions": 50, "students": 5}),
                created_at=now,
            )
        )

        db.commit()
        print("Database successfully seeded with 10 concepts, 50 questions, and sample student profiles!")
    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    should_reset = "--reset" in sys.argv or "-r" in sys.argv
    seed_database(drop_existing=should_reset)
