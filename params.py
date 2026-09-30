"""Configurable hyperparameters for MasteryFlow Engine.
Versioned, serialized to JSON, fully deterministic.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, Any


@dataclass
class EngineConfig:
    version: str = "v1.0.0"
    
    # Mastery thresholds
    mastery_threshold: float = 0.80          # Threshold for proficient/mastery readiness
    prereq_readiness_threshold: float = 0.65 # Ancestor readiness needed to learn child
    easy_cap: float = 0.70                   # Mastery ceiling without transfer or d>=3 passed
    uncertainty_threshold: float = 0.35      # High certainty threshold
    
    # Forgetting model parameters
    half_life_days: float = 14.0             # Half-life for mastery decay in days
    floor_mastery: float = 0.20              # Asymptotic floor for decay
    long_gap_seconds: float = 7 * 86400.0    # 7 days gap threshold for 'forgetting' classification
    
    # Intervention triggers
    stuck_cycles_threshold: int = 3          # Consecutive failures triggering Teacher Intervention
    stagnation_attempts: int = 5             # Window to detect stagnation
    stagnation_delta: float = 0.05           # Max delta considered stagnation
    intervention_high_uncertainty_attempts: int = 8
    intervention_high_uncertainty_threshold: float = 0.45

    # Evidence weighting penalties
    rapid_guess_procedural_ms: int = 3000
    rapid_guess_recall_ms: int = 1500
    rapid_retry_window_seconds: float = 60.0
    hint_penalties: Dict[int, float] = field(default_factory=lambda: {
        0: 1.0,
        1: 0.70,
        2: 0.45,
        3: 0.15,
    })
    low_confidence_discount: float = 0.40    # Weight multiplier if correct but confidence == 1

    # Base Bayesian knowledge tracing slips & guesses
    base_guesses: Dict[str, float] = field(default_factory=lambda: {
        "recall": 0.25,
        "procedural": 0.20,
        "transfer": 0.10,
    })
    base_slips: Dict[str, float] = field(default_factory=lambda: {
        "recall": 0.08,
        "procedural": 0.12,
        "transfer": 0.18,
    })

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EngineConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


DEFAULT_CONFIG = EngineConfig()
