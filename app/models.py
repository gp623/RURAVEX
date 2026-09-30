"""SQLAlchemy ORM models for MasteryFlow.
Strict foreign keys, constraints, and indexes.
"""

from __future__ import annotations
import json
import time
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    Text,
    ForeignKey,
    Index,
    CheckConstraint,
    PrimaryKeyConstraint,
)
from sqlalchemy.orm import relationship
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False)  # 'student' | 'teacher'
    created_at = Column(Float, default=lambda: time.time(), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('student', 'teacher')", name="chk_user_role"),
    )

    attempts = relationship("Attempt", back_populates="student", cascade="all, delete-orphan")
    mastery_states = relationship("MasteryStateRecord", back_populates="student", cascade="all, delete-orphan")
    decisions = relationship("DecisionRecord", back_populates="student", cascade="all, delete-orphan")


class ConceptRecord(Base):
    __tablename__ = "concepts"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=False)
    domain = Column(String(64), nullable=False, default="fractions_to_percentages")
    tier = Column(Integer, nullable=False, default=1)

    questions = relationship("QuestionRecord", back_populates="concept")


class ConceptPrereq(Base):
    __tablename__ = "concept_prereqs"

    concept_id = Column(String(64), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)
    prereq_id = Column(String(64), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("concept_id", "prereq_id"),
    )


class QuestionRecord(Base):
    __tablename__ = "questions"

    id = Column(String(64), primary_key=True)
    concept_id = Column(String(64), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False)
    difficulty = Column(Integer, nullable=False)  # 1 to 5
    type = Column(String(32), nullable=False)      # 'recall' | 'procedural' | 'transfer'
    prompt = Column(Text, nullable=False)
    answer = Column(String(256), nullable=False)
    wrong_answers_json = Column(Text, nullable=False, default="[]")
    hints_json = Column(Text, nullable=False, default="[]")
    created_at = Column(Float, default=lambda: time.time(), nullable=False)

    __table_args__ = (
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="chk_question_difficulty"),
        CheckConstraint("type IN ('recall', 'procedural', 'transfer')", name="chk_question_type"),
    )

    concept = relationship("ConceptRecord", back_populates="questions")


class Attempt(Base):
    __tablename__ = "attempts"

    id = Column(String(64), primary_key=True)
    student_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(String(64), ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False)
    concept_id = Column(String(64), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False)
    correct = Column(Boolean, nullable=False)
    confidence = Column(Integer, nullable=False)
    response_ms = Column(Integer, nullable=False)
    hints_used = Column(Integer, nullable=False, default=0)
    attempt_no = Column(Integer, nullable=False, default=1)
    weight_applied = Column(Float, nullable=False, default=1.0)
    created_at = Column(Float, default=lambda: time.time(), nullable=False)

    __table_args__ = (
        CheckConstraint("confidence BETWEEN 1 AND 5", name="chk_attempt_confidence"),
        CheckConstraint("hints_used BETWEEN 0 AND 3", name="chk_attempt_hints"),
        Index("idx_attempts_student_created", "student_id", "created_at"),
    )

    student = relationship("User", back_populates="attempts")


class MasteryStateRecord(Base):
    __tablename__ = "mastery_state"

    student_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    concept_id = Column(String(64), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)
    p_mastery = Column(Float, nullable=False, default=0.15)
    uncertainty = Column(Float, nullable=False, default=0.85)
    has_passed_transfer = Column(Boolean, nullable=False, default=False)
    highest_difficulty_passed = Column(Integer, nullable=False, default=0)
    last_evidence_at = Column(Float, default=lambda: time.time(), nullable=False)
    status = Column(String(32), nullable=False, default="unencountered")
    consecutive_failures = Column(Integer, nullable=False, default=0)
    total_attempts = Column(Integer, nullable=False, default=0)
    history_json = Column(Text, nullable=False, default="[]")

    __table_args__ = (
        PrimaryKeyConstraint("student_id", "concept_id"),
        CheckConstraint("p_mastery BETWEEN 0.0 AND 1.0", name="chk_mastery_range"),
        CheckConstraint("uncertainty BETWEEN 0.0 AND 1.0", name="chk_uncertainty_range"),
        CheckConstraint(
            "status IN ('unencountered', 'struggling', 'learning', 'proficient', 'mastered')",
            name="chk_mastery_status",
        ),
    )

    student = relationship("User", back_populates="mastery_states")


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id = Column(String(64), primary_key=True)
    student_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(64), nullable=False)
    target_concept_id = Column(String(64), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False)
    reason = Column(Text, nullable=False)
    counterfactual_json = Column(Text, nullable=False, default="{}")
    config_version = Column(String(32), nullable=False, default="v1.0.0")
    state_snapshot_json = Column(Text, nullable=False)
    evidence_ids_json = Column(Text, nullable=False, default="[]")
    created_at = Column(Float, default=lambda: time.time(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "action IN ('Advance', 'Practice', 'Review', 'Remediate Prerequisite', 'Challenge', 'Teacher Intervention')",
            name="chk_decision_action",
        ),
        Index("idx_decisions_student_created", "student_id", "created_at"),
    )

    student = relationship("User", back_populates="decisions")
    overrides = relationship("OverrideRecord", back_populates="decision", cascade="all, delete-orphan")


class OverrideRecord(Base):
    __tablename__ = "overrides"

    id = Column(String(64), primary_key=True)
    decision_id = Column(String(64), ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False)
    student_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    teacher_id = Column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    original_action = Column(String(64), nullable=False)
    new_action = Column(String(64), nullable=False)
    new_target_concept_id = Column(String(64), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False)
    note = Column(Text, nullable=False)
    created_at = Column(Float, default=lambda: time.time(), nullable=False)

    __table_args__ = (
        CheckConstraint("length(trim(note)) >= 5", name="chk_override_note_nonempty"),
    )

    decision = relationship("DecisionRecord", back_populates="overrides")


class EngineConfigRecord(Base):
    __tablename__ = "engine_config"

    version = Column(String(32), primary_key=True)
    params_json = Column(Text, nullable=False)
    created_at = Column(Float, default=lambda: time.time(), nullable=False)
    created_by = Column(String(64), nullable=False, default="system")


class AuditLogRecord(Base):
    __tablename__ = "audit_log"

    id = Column(String(64), primary_key=True)
    actor_id = Column(String(64), nullable=False)
    event = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(Float, default=lambda: time.time(), nullable=False)

    __table_args__ = (
        Index("idx_audit_actor_created", "actor_id", "created_at"),
    )
