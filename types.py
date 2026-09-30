"""Data types and schemas for MasteryFlow Engine.
Zero external framework dependencies.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone


class QuestionType(str, Enum):
    RECALL = "recall"
    PROCEDURAL = "procedural"
    TRANSFER = "transfer"


class Action(str, Enum):
    ADVANCE = "Advance"
    PRACTICE = "Practice"
    REVIEW = "Review"
    REMEDIATE = "Remediate Prerequisite"
    CHALLENGE = "Challenge"
    INTERVENTION = "Teacher Intervention"


class MasteryStatus(str, Enum):
    UNENCOUNTERED = "unencountered"
    STRUGGLING = "struggling"
    LEARNING = "learning"
    PROFICIENT = "proficient"
    MASTERED = "mastered"


@dataclass
class Concept:
    id: str
    name: str
    description: str
    domain: str = "fractions_to_percentages"
    tier: int = 1
    prerequisites: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Question:
    id: str
    concept_id: str
    difficulty: int  # 1 to 5
    type: QuestionType
    prompt: str
    answer: str
    wrong_answers: List[Dict[str, str]] = field(default_factory=list)  # [{"option": "...", "misconception": "..."}]
    hints: List[str] = field(default_factory=list)  # Progressive hints [tier1, tier2, tier3]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


@dataclass
class Attempt:
    id: str
    student_id: str
    question_id: str
    concept_id: str
    correct: bool
    confidence: int  # 1 to 5
    response_ms: int
    hints_used: int = 0  # 0 to 3
    attempt_no: int = 1
    created_at: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    weight_applied: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MasteryState:
    student_id: str
    concept_id: str
    p_mastery: float = 0.15
    uncertainty: float = 0.85
    has_passed_transfer: bool = False
    highest_difficulty_passed: int = 0
    last_evidence_at: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    status: MasteryStatus = MasteryStatus.UNENCOUNTERED
    consecutive_failures: int = 0
    total_attempts: int = 0
    recent_history: List[bool] = field(default_factory=list)  # Up to last 10 outcomes
    history_weights: List[float] = field(default_factory=list) # Weights of recent attempts

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = MasteryStatus(self.status)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, Enum) else str(self.status)
        return d


@dataclass
class CounterfactualResult:
    current_action: Action
    counterfactual_action: Action
    required_condition: str
    target_concept_id: str
    projected_mastery: float
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_action": self.current_action.value,
            "counterfactual_action": self.counterfactual_action.value,
            "required_condition": self.required_condition,
            "target_concept_id": self.target_concept_id,
            "projected_mastery": round(self.projected_mastery, 3),
            "explanation": self.explanation,
        }


@dataclass
class Decision:
    id: str
    student_id: str
    action: Action
    target_concept_id: str
    reason: str
    counterfactual: Dict[str, Any]
    config_version: str
    state_snapshot: Dict[str, Any]
    evidence_ids: List[str]
    created_at: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d
