"""Pydantic schemas for request validation and response serialization."""

from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# --- Auth & User ---
class UserLogin(BaseModel):
    user_id: str
    role: Optional[str] = None  # 'student' | 'teacher'


class UserOut(BaseModel):
    id: str
    name: str
    role: str
    created_at: float


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- Concepts & Graph ---
class ConceptOut(BaseModel):
    id: str
    name: str
    description: str
    domain: str
    tier: int
    prerequisites: List[str] = []


class CurriculumGraphOut(BaseModel):
    concepts: List[ConceptOut]
    edges: List[Dict[str, str]]


# --- Mastery State ---
class MasteryStateOut(BaseModel):
    student_id: str
    concept_id: str
    concept_name: Optional[str] = None
    p_mastery: float
    uncertainty: float
    has_passed_transfer: bool
    highest_difficulty_passed: int
    last_evidence_at: float
    status: str
    consecutive_failures: int
    total_attempts: int
    recent_history: List[bool] = []


class StudentStateResponse(BaseModel):
    student_id: str
    states: Dict[str, MasteryStateOut]


# --- Diagnostic ---
class DiagnosticStartResponse(BaseModel):
    session_id: str
    total_steps: int = 6
    current_step: int
    question: Dict[str, Any]


class DiagnosticAnswerRequest(BaseModel):
    concept_id: str
    question_id: str
    selected_answer: str
    confidence: int = Field(ge=1, le=5)
    response_ms: int = Field(ge=100)


class DiagnosticAnswerResponse(BaseModel):
    step_completed: int
    total_steps: int = 6
    is_finished: bool
    correct: bool
    explanation: str
    next_question: Optional[Dict[str, Any]] = None
    inferred_states: Optional[Dict[str, MasteryStateOut]] = None


# --- Questions & Attempts ---
class QuestionOut(BaseModel):
    id: str
    concept_id: str
    concept_name: Optional[str] = None
    difficulty: int
    type: str
    prompt: str
    options: List[str]
    hints: List[str]


class NextQuestionResponse(BaseModel):
    question: Optional[QuestionOut]
    recommendation: Dict[str, Any]


class AttemptSubmitRequest(BaseModel):
    question_id: str
    concept_id: str
    answer: str
    confidence: int = Field(ge=1, le=5)
    response_ms: int = Field(ge=100)
    hints_used: int = Field(ge=0, le=3, default=0)
    attempt_no: int = Field(ge=1, default=1)


class AttemptSubmitResponse(BaseModel):
    correct: bool
    weight_applied: float
    feedback: str
    misconception: Optional[str] = None
    new_mastery: float
    new_uncertainty: float
    delta_p: float
    status: str
    next_action: str
    target_concept_id: str
    reason: str
    counterfactual: Dict[str, Any]


# --- Recommendations & Agency ---
class CounterfactualOut(BaseModel):
    current_action: str
    counterfactual_action: str
    required_condition: str
    target_concept_id: str
    projected_mastery: float
    explanation: str


class RecommendationOut(BaseModel):
    student_id: str
    action: str
    target_concept_id: str
    target_concept_name: str
    reason: str
    counterfactual: CounterfactualOut
    config_version: str
    created_at: float
    is_override: bool = False
    override_note: Optional[str] = None


class AgencyRequest(BaseModel):
    chosen_concept_id: str
    reason: Optional[str] = None


class AgencyResponse(BaseModel):
    success: bool
    message: str
    target_concept_id: str
    target_concept_name: str


# --- Teacher Endpoints ---
class ClassHeatmapItem(BaseModel):
    student_id: str
    student_name: str
    concepts: Dict[str, Dict[str, Any]]  # concept_id -> {p_mastery, uncertainty, status}
    overall_progress: float


class StuckStudentItem(BaseModel):
    student_id: str
    student_name: str
    concept_id: str
    concept_name: str
    consecutive_failures: int
    total_attempts: int
    p_mastery: float
    uncertainty: float
    status: str
    stuck_reason: str


class StudentPathItem(BaseModel):
    timestamp: float
    event_type: str  # 'attempt' | 'decision' | 'override'
    details: Dict[str, Any]


class TeacherOverrideRequest(BaseModel):
    decision_id: str
    student_id: str
    new_action: str
    new_target_concept_id: str
    note: str = Field(min_length=6)


class ConfigUpdateRequest(BaseModel):
    params: Dict[str, Any]
    comment: Optional[str] = "Teacher config update"


# --- AI Sandbox ---
class AIGenerateQuestionRequest(BaseModel):
    concept_id: str
    difficulty: int = Field(ge=1, le=5)
    type: str  # 'recall' | 'procedural' | 'transfer'


class AIGenerateQuestionResponse(BaseModel):
    question: Optional[QuestionOut]
    verified: bool
    ai_status: str  # 'ai_generated' | 'fallback_template' | 'ai_unavailable'
    validation_log: str


class AIExplainRequest(BaseModel):
    numeric_reason: str
    student_id: str


class AIExplainResponse(BaseModel):
    friendly_explanation: str
    ai_status: str  # 'ai_rewritten' | 'fallback_original' | 'ai_unavailable'
    numbers_preserved: bool
