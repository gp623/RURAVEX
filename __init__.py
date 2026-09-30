"""MasteryFlow Engine package. Pure Python deterministic adaptive learning engine."""

from engine.types import (
    Action,
    Attempt,
    Concept,
    CounterfactualResult,
    Decision,
    MasteryState,
    MasteryStatus,
    Question,
    QuestionType,
)
from engine.params import EngineConfig, DEFAULT_CONFIG
from engine.bayesian import (
    compute_slip_guess,
    compute_evidence_weight,
    update_mastery_bayesian,
)
from engine.forgetting import apply_forgetting, is_long_gap
from engine.graph import CurriculumGraph
from engine.recommender import (
    determine_next_action,
    generate_counterfactual,
    evaluate_and_record_decision,
    replay_attempts,
)

__all__ = [
    "Action",
    "Attempt",
    "Concept",
    "CounterfactualResult",
    "Decision",
    "MasteryState",
    "MasteryStatus",
    "Question",
    "QuestionType",
    "EngineConfig",
    "DEFAULT_CONFIG",
    "compute_slip_guess",
    "compute_evidence_weight",
    "update_mastery_bayesian",
    "apply_forgetting",
    "is_long_gap",
    "CurriculumGraph",
    "determine_next_action",
    "generate_counterfactual",
    "evaluate_and_record_decision",
    "replay_attempts",
]
